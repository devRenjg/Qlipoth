from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from searcher import grep_search, read_file_content
from llm import generate_search_strategy, generate_answer, stream_answer
from config import load_settings
from database import DB_PATH
from datetime import datetime, timezone, timedelta
import aiosqlite
import re
import time
import json

router = APIRouter(tags=["query"])

_BJ_TZ = timezone(timedelta(hours=8))


def _now_bj() -> str:
    return datetime.now(_BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]
    source_urls: list[dict] = []
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
        files_to_read = _select_files(results)
        search_text_parts = []
        total_chars = 0
        per_file_budget = MAX_CONTEXT_CHARS // min(len(files_to_read), 10)
        for f in files_to_read:
            content = read_file_content(f)
            if len(content) <= per_file_budget:
                chunk = f"=== 文件: {f} ===\n{content}\n"
            else:
                file_results = [r for r in results if r.file == f]
                excerpts = _extract_relevant_sections(content, file_results, budget=per_file_budget)
                chunk = f"=== 文件: {f} (摘录) ===\n{excerpts}\n"
            if total_chars + len(chunk) > MAX_CONTEXT_CHARS:
                break
            search_text_parts.append(chunk)
            total_chars += len(chunk)
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
    source_urls = _extract_source_urls(files_to_read if results else [])

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

    return QueryResponse(answer=answer, sources=sources, source_urls=source_urls, search_strategy=strategy, timing=timing)


def _extract_source_urls(files: list[str]) -> list[dict]:
    """Extract source references from files. Returns top 5 most relevant.
    For URL-imported docs: returns online link. For file uploads: returns local file reference."""
    source_urls = []
    seen = set()
    for f in files:
        if f in seen:
            continue
        seen.add(f)
        content = read_file_content(f)
        title = f.replace(".md", "")
        url = None
        for line in content.split("\n")[:10]:
            m = re.match(r'>\s*来源:\s*(https?://[^\s]+)', line)
            if m:
                url = m.group(1)
                cut = re.search(r'[一-鿿$\[\]()（）]', url)
                if cut:
                    url = url[:cut.start()]
                url = url.rstrip("&?#")
                if "weixin.qq.com" not in url and "docs.qq.com" not in url:
                    url = None
                break
        source_urls.append({"title": title, "url": url, "file": f})
        if len(source_urls) >= 5:
            break
    return source_urls


_KB_DOC_COUNT_CACHE = {"n": 0, "ts": 0.0}


def _kb_doc_count() -> int:
    """知识库 md 文档总数,60s 缓存,用于 IDF 计算。"""
    import os
    now = time.time()
    if now - _KB_DOC_COUNT_CACHE["ts"] < 60 and _KB_DOC_COUNT_CACHE["n"] > 0:
        return _KB_DOC_COUNT_CACHE["n"]
    settings = load_settings()
    try:
        n = sum(1 for f in os.listdir(settings.knowledge_base_dir) if f.endswith(".md"))
    except OSError:
        n = 0
    _KB_DOC_COUNT_CACHE["n"] = max(n, 1)
    _KB_DOC_COUNT_CACHE["ts"] = now
    return _KB_DOC_COUNT_CACHE["n"]


def _select_files(results: list, max_files: int = 10) -> list[str]:
    """选取最相关文件(第二波优化,已用 golden set 离线验证):
    打分 = Σ IDF(命中的不同关键词) × (1 + 文件名命中加权)。
    - 按"文件命中的不同关键词"累加 IDF,而非按命中行累加:命中稀有词(如"版本覆盖率",
      全库仅 3 篇)一次,远胜命中宽泛词(如"春晚",263 篇)五十次。这修正了第一波纯 IDF
      按行累加被巨型文档统治的问题。
    - 文件名含原始关键词时整体加权,稳住"文件名即强信号"的收益。
    - 离线对照(固定关键词):命中率 0.43(baseline)→0.59(第一波)→0.82(本版), MRR 0.24→0.38→0.65。
    """
    import math
    from collections import defaultdict
    if not results:
        return []

    original_keywords = getattr(results, "original_keywords", []) or []
    keyword_df = getattr(results, "keyword_df", {}) or {}
    total_docs = _kb_doc_count()

    def idf(kw: str) -> float:
        df = keyword_df.get(kw, 1)
        return math.log((total_docs + 1) / (df + 1)) + 0.5

    def filename_hit(fname: str) -> float:
        core = fname.rsplit(".", 1)[0]
        best = 0.0
        for kw in original_keywords:
            if not kw or len(kw) < 2:
                continue
            if kw in core:
                best = max(best, 2.0)
            elif len(kw) >= 3 and kw[:-1] in core:
                best = max(best, 0.8)
        return best

    file_kws = defaultdict(set)
    for r in results:
        kw = getattr(r, "matched_keyword", "") or ""
        if kw:
            file_kws[r.file].add(kw)

    score = {}
    for f, kws in file_kws.items():
        coverage = sum(idf(kw) for kw in kws)
        score[f] = coverage * (1.0 + filename_hit(f))

    ranked = sorted(score, key=lambda f: (-score[f], f))
    return ranked[:max_files]



def _extract_relevant_sections(content: str, results: list, context_radius: int = 20, budget: int = None) -> str:
    """Extract sections around search hits, ensuring coverage across different document sections.
    Prioritizes summary/statistics sections."""
    max_chars = budget if budget else MAX_CONTEXT_CHARS
    lines = content.splitlines()
    total_lines = len(lines)

    section_starts = [0]
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section_starts.append(i)
    section_starts.append(total_lines)

    # Identify all sections with hits
    sections = []
    for idx in range(len(section_starts) - 1):
        sec_start = section_starts[idx]
        sec_end = section_starts[idx + 1]
        sec_hits = [r for r in results if sec_start < r.line_number <= sec_end]
        if sec_hits:
            sec_title = lines[sec_start] if sec_start < total_lines else ""
            is_summary = any(kw in sec_title for kw in ("统计", "总计", "汇总", "合计", "概览", "总结"))
            sections.append((sec_start, sec_end, sec_hits, is_summary))

    # Also include summary sections even without direct hits
    for idx in range(len(section_starts) - 1):
        sec_start = section_starts[idx]
        sec_end = section_starts[idx + 1]
        sec_title = lines[sec_start] if sec_start < total_lines else ""
        if any(kw in sec_title for kw in ("统计", "总计", "汇总", "合计", "概览", "总结")):
            already = any(s[0] == sec_start for s in sections)
            if not already:
                sections.append((sec_start, sec_end, [], True))

    # Sort: summary sections first, then by hit count
    sections.sort(key=lambda x: (not x[3], -len(x[2])))

    parts = []
    total_chars = 0

    # Give summary sections 50% of budget, rest share the other 50%
    summary_count = sum(1 for s in sections if s[3])
    non_summary_count = len(sections) - summary_count
    if summary_count > 0 and non_summary_count > 0:
        summary_budget = (max_chars // 2) // summary_count
        normal_budget = (max_chars // 2) // non_summary_count
    elif summary_count > 0:
        summary_budget = max_chars // summary_count
        normal_budget = 0
    else:
        summary_budget = 0
        normal_budget = max_chars // max(non_summary_count, 1)

    for sec_start, sec_end, sec_hits, is_summary in sections:
        budget_per_section = summary_budget if is_summary else normal_budget
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
                    remaining = budget_per_section - sec_chars
                    if remaining > 100:
                        sec_parts.append(snippet[:remaining] + "\n...(截断)")
                        sec_chars += remaining
                    break
                sec_parts.append(snippet)
                sec_chars += len(snippet)
            chunk = f"[行 {sec_start+1}+ 摘录]:\n" + "\n...\n".join(sec_parts)

        if total_chars + len(chunk) > max_chars:
            remaining = max_chars - total_chars
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
        files_to_read = _select_files(results)
        search_text_parts = []
        total_chars = 0
        per_file_budget = MAX_CONTEXT_CHARS // min(len(files_to_read), 10)
        for f in files_to_read:
            content = read_file_content(f)
            if len(content) <= per_file_budget:
                chunk = f"=== 文件: {f} ===\n{content}\n"
            else:
                file_results = [r for r in results if r.file == f]
                excerpts = _extract_relevant_sections(content, file_results, budget=per_file_budget)
                chunk = f"=== 文件: {f} (摘录) ===\n{excerpts}\n"
            if total_chars + len(chunk) > MAX_CONTEXT_CHARS:
                break
            search_text_parts.append(chunk)
            total_chars += len(chunk)
        search_text = "\n".join(search_text_parts)
    else:
        search_text = "未找到相关内容。"

    sources = [{"file": r.file, "line": r.line_number, "content": r.content} for r in results[:10]]
    source_urls = _extract_source_urls(files_to_read if results else [])
    t_prep = time.perf_counter() - t_start

    async def event_generator():
        meta = {
            "sources": sources,
            "source_urls": source_urls,
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


class SaveHistoryRequest(BaseModel):
    question: str
    answer: str
    source_urls: list[dict] = []
    user_id: int | None = None


@router.post("/chat/history")
async def save_chat_history(req: SaveHistoryRequest):
    """Save a Q&A pair to chat history."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chat_history (user_id, question, answer, source_urls, created_at) VALUES (?, ?, ?, ?, ?)",
            (req.user_id, req.question, req.answer, json.dumps(req.source_urls, ensure_ascii=False), _now_bj()),
        )
        await db.commit()
    return {"message": "saved"}


@router.get("/chat/history")
async def get_chat_history(user_id: int | None = None, limit: int = 50):
    """Get chat history, optionally filtered by user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if user_id:
            cursor = await db.execute(
                "SELECT h.*, u.username as user_name FROM chat_history h LEFT JOIN users u ON h.user_id = u.id WHERE h.user_id = ? ORDER BY h.created_at DESC LIMIT ?",
                (user_id, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT h.*, u.username as user_name FROM chat_history h LEFT JOIN users u ON h.user_id = u.id ORDER BY h.created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "question": row["question"],
                "answer": row["answer"],
                "source_urls": json.loads(row["source_urls"]) if row["source_urls"] else [],
                "created_at": row["created_at"],
                "user_name": row["user_name"] or "匿名",
            }
            for row in rows
        ]


@router.delete("/chat/history/{history_id}")
async def delete_chat_history(history_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chat_history WHERE id = ?", (history_id,))
        await db.commit()
    return {"message": "deleted"}
