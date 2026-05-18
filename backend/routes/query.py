from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from searcher import grep_search, read_file_content, list_files
from llm import generate_search_strategy, generate_answer
from config import load_settings

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]
    search_strategy: dict


@router.post("/query", response_model=QueryResponse)
async def query_knowledge_base(req: QueryRequest):
    settings = load_settings()
    if not settings.llm_api_key:
        raise HTTPException(400, "请先在设置中配置 LLM API Key")

    strategy = await generate_search_strategy(req.question)

    keywords = strategy.get("keywords", [req.question])
    file_pattern = strategy.get("file_pattern", "*")
    need_full_file = strategy.get("need_full_file", False)

    results = grep_search(keywords, file_pattern)

    if need_full_file and results:
        files_to_read = list({r.file for r in results[:3]})
        search_text_parts = []
        for f in files_to_read:
            content = read_file_content(f)
            search_text_parts.append(f"=== 文件: {f} ===\n{content}\n")
        search_text = "\n".join(search_text_parts)
    else:
        search_text_parts = []
        for r in results[:20]:
            context = "\n".join(r.context_before + [f">>> {r.content}"] + r.context_after)
            search_text_parts.append(f"[{r.file}:{r.line_number}]\n{context}\n")
        search_text = "\n".join(search_text_parts)

    if not search_text.strip():
        search_text = "未找到相关内容。"

    answer = await generate_answer(req.question, search_text)

    sources = [{"file": r.file, "line": r.line_number, "content": r.content} for r in results[:10]]

    return QueryResponse(answer=answer, sources=sources, search_strategy=strategy)


@router.get("/search")
async def simple_search(q: str):
    results = grep_search([q])
    return {
        "results": [
            {"file": r.file, "line": r.line_number, "content": r.content}
            for r in results[:30]
        ]
    }
