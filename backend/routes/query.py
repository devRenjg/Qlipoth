from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from searcher import grep_search, read_file_content
from llm import generate_search_strategy, generate_answer, stream_answer
from config import load_settings
import time
import json

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]
    search_strategy: dict
    timing: dict


MAX_CONTEXT_CHARS = 60000


@router.post("/query", response_model=QueryResponse)
async def query_knowledge_base(req: QueryRequest):
    t_start = time.perf_counter()

    settings = load_settings()
    if not settings.llm_api_key:
        raise HTTPException(400, "请先在设置中配置 LLM API Key")

    try:
        t0 = time.perf_counter()
        strategy, strategy_llm_time = await generate_search_strategy(req.question)
        t_strategy = time.perf_counter() - t0
    except RuntimeError as e:
        raise HTTPException(502, f"LLM 搜索策略生成失败: {e}")

    keywords = strategy.get("keywords", [req.question])
    file_pattern = strategy.get("file_pattern", "*")

    t0 = time.perf_counter()
    results = grep_search(keywords, file_pattern)
    t_search = time.perf_counter() - t0

    t0 = time.perf_counter()
    if results:
        files_to_read = list({r.file for r in results[:5]})
        search_text_parts = []
        for f in files_to_read:
            content = read_file_content(f)
            if len(content) <= MAX_CONTEXT_CHARS:
                search_text_parts.append(f"=== 文件: {f} ===\n{content}\n")
            else:
                file_results = [r for r in results if r.file == f]
                excerpts = _extract_relevant_sections(content, file_results)
                search_text_parts.append(f"=== 文件: {f} (摘录) ===\n{excerpts}\n")
        search_text = "\n".join(search_text_parts)
    else:
        search_text = "未找到相关内容。"
    t_extract = time.perf_counter() - t0

    try:
        t0 = time.perf_counter()
        answer, answer_llm_time = await generate_answer(req.question, search_text)
        t_answer = time.perf_counter() - t0
    except RuntimeError as e:
        raise HTTPException(502, f"LLM 回答生成失败: {e}")

    t_total = time.perf_counter() - t_start

    sources = [{"file": r.file, "line": r.line_number, "content": r.content} for r in results[:10]]

    timing = {
        "total": round(t_total, 2),
        "strategy": round(t_strategy, 2),
        "strategy_llm": round(strategy_llm_time, 2),
        "search": round(t_search, 2),
        "extract": round(t_extract, 2),
        "answer": round(t_answer, 2),
        "answer_llm": round(answer_llm_time, 2),
        "search_results_count": len(results),
        "context_chars": len(search_text),
    }

    return QueryResponse(answer=answer, sources=sources, search_strategy=strategy, timing=timing)


def _extract_relevant_sections(content: str, results: list, context_radius: int = 20) -> str:
    """Extract sections around search hits, ensuring coverage across different document sections."""
    lines = content.splitlines()
    total_lines = len(lines)

    section_starts = [0]
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section_starts.append(i)
    section_starts.append(total_lines)

    sections = []
    for idx in range(len(section_starts) - 1):
        sec_start = section_starts[idx]
        sec_end = section_starts[idx + 1]
        sec_hits = [r for r in results if sec_start < r.line_number <= sec_end]
        if sec_hits:
            sections.append((sec_start, sec_end, sec_hits))

    parts = []
    total_chars = 0
    budget_per_section = MAX_CONTEXT_CHARS // max(len(sections), 1)

    for sec_start, sec_end, sec_hits in sections:
        sec_lines = lines[sec_start:sec_end]
        sec_content = "\n".join(sec_lines)

        if len(sec_content) <= budget_per_section:
            chunk = f"[行 {sec_start+1}-{sec_end}]:\n{sec_content}"
        else:
            hit_indices = sorted({r.line_number - 1 - sec_start for r in sec_hits})
            ranges = []
            for hit in hit_indices:
                s = max(0, hit - context_radius)
                e = min(len(sec_lines), hit + context_radius + 1)
                if ranges and s <= ranges[-1][1]:
                    ranges[-1] = (ranges[-1][0], e)
                else:
                    ranges.append((s, e))

            sec_parts = []
            sec_chars = 0
            for s, e in ranges:
                snippet = "\n".join(sec_lines[s:e])
                if sec_chars + len(snippet) > budget_per_section:
                    break
                sec_parts.append(snippet)
                sec_chars += len(snippet)
            chunk = f"[行 {sec_start+1}+ 摘录]:\n" + "\n...\n".join(sec_parts)

        if total_chars + len(chunk) > MAX_CONTEXT_CHARS:
            remaining = MAX_CONTEXT_CHARS - total_chars
            if remaining > 200:
                parts.append(chunk[:remaining] + "\n...(截断)")
            break
        parts.append(chunk)
        total_chars += len(chunk)

    return "\n\n".join(parts)


@router.get("/search")
async def simple_search(q: str):
    results = grep_search([q])
    return {
        "results": [
            {"file": r.file, "line": r.line_number, "content": r.content}
            for r in results[:30]
        ]
    }


@router.post("/query/stream")
async def query_knowledge_base_stream(req: QueryRequest):
    settings = load_settings()
    if not settings.llm_api_key:
        raise HTTPException(400, "请先在设置中配置 LLM API Key")

    t_start = time.perf_counter()

    try:
        strategy, strategy_llm_time = await generate_search_strategy(req.question)
    except RuntimeError as e:
        raise HTTPException(502, f"LLM 搜索策略生成失败: {e}")

    keywords = strategy.get("keywords", [req.question])
    file_pattern = strategy.get("file_pattern", "*")

    t0 = time.perf_counter()
    results = grep_search(keywords, file_pattern)
    t_search = time.perf_counter() - t0

    if results:
        files_to_read = list({r.file for r in results[:5]})
        search_text_parts = []
        for f in files_to_read:
            content = read_file_content(f)
            if len(content) <= MAX_CONTEXT_CHARS:
                search_text_parts.append(f"=== 文件: {f} ===\n{content}\n")
            else:
                file_results = [r for r in results if r.file == f]
                excerpts = _extract_relevant_sections(content, file_results)
                search_text_parts.append(f"=== 文件: {f} (摘录) ===\n{excerpts}\n")
        search_text = "\n".join(search_text_parts)
    else:
        search_text = "未找到相关内容。"

    sources = [{"file": r.file, "line": r.line_number, "content": r.content} for r in results[:10]]
    t_prep = time.perf_counter() - t_start

    async def event_generator():
        meta = {
            "sources": sources,
            "search_strategy": strategy,
            "timing_prep": round(t_prep, 2),
            "timing_strategy": round(strategy_llm_time, 2),
            "timing_search": round(t_search, 2),
            "search_results_count": len(results),
            "context_chars": len(search_text),
        }
        yield f"data: {json.dumps({'type': 'meta', 'data': meta}, ensure_ascii=False)}\n\n"

        t0 = time.perf_counter()
        try:
            async for chunk in stream_answer(req.question, search_text):
                yield f"data: {json.dumps({'type': 'chunk', 'data': chunk}, ensure_ascii=False)}\n\n"
        except RuntimeError as e:
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)}, ensure_ascii=False)}\n\n"
            return

        t_answer = time.perf_counter() - t0
        t_total = time.perf_counter() - t_start
        timing = {
            "total": round(t_total, 2),
            "strategy": round(strategy_llm_time, 2),
            "search": round(t_search, 2),
            "answer": round(t_answer, 2),
            "search_results_count": len(results),
            "context_chars": len(search_text),
        }
        yield f"data: {json.dumps({'type': 'done', 'data': timing}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
