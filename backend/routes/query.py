from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from searcher import grep_search, read_file_content, SearchResults
from llm import generate_search_strategy, generate_answer, stream_answer, resolve_coreference
from config import load_settings
from question_router import route_model
from database import DB_PATH
from auth import require_login
from datetime import datetime, timezone, timedelta
import aiosqlite
import re
import time
import json

router = APIRouter(tags=["query"])

_BJ_TZ = timezone(timedelta(hours=8))


def _now_bj() -> str:
    return datetime.now(_BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _parse_tags(raw) -> list[dict]:
    """安全解析 chat_history.selected_tags（JSON 文本）→ [{id,name}]，旧行/空值返回 []。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


class QueryRequest(BaseModel):
    question: str
    conversation_id: str | None = None
    tag_ids: list[int] = []


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]
    source_urls: list[dict] = []
    relevant_images: list[dict] = []
    search_strategy: dict
    timing: dict


MAX_CONTEXT_CHARS = 60000

# 检索摘录优化开关（默认开）：True=按关键词密度优先摘录 + "摘要"块高优先级；
# False=旧行为（按行号顺序摘录、摘要块不特殊优先）。仅供 A/B 评测对照临时关闭。
_RETRIEVAL_OPT = True

# 相关图片（方案A 画廊）：从被选入上下文的正文里收干净内容图，按相关文件位置关联。
# 正则与前端 utils/markdown.js 的 IMG_PATTERNS 对齐：仅识别已知图床内容图，
# 用干净边界（?w=&h=&type=image/ 或显式扩展名）砍掉 protobuf 残片乱码尾巴。
MAX_RELEVANT_IMAGES = 8
MAX_IMAGES_PER_FILE = 4  # 单文档限额，避免一篇图多文档独占画廊，保证跨文档多样性
_IMG_PATTERNS = [
    re.compile(r'https?://[\w.-]*qpic\.cn/[\w%./~-]+\?w=\d+&h=\d+&type=image/[a-z]+', re.I),
    re.compile(
        r'https?://(?:[\w-]+\.)?(?:qpic\.cn|gtimg\.cn|example\.com)'
        r'/[\w%./~-]+\.(?:png|jpe?g|gif|webp|bmp)(?:@[\w_]+)?',
        re.I,
    ),
]


def _collect_images_into(text: str, file: str, images: list[dict], seen: set[str], limit: int) -> None:
    """从一个被选为 top-N 相关的文档正文里抽干净内容图，去重后追加到 images（至多 limit 张）。

    相关性信号是文件级：这些文件正是文字答案的来源（BM25+RRF top-N），其内容图即相关图。
    比段落级位置关联召回高得多——图片 URL 极少恰好落在命中行 ±20 的摘录窗口内。
    """
    title = file.replace(".md", "")
    added = 0
    for pat in _IMG_PATTERNS:
        for m in pat.finditer(text):
            if added >= limit:
                return
            url = m.group(0)
            if url not in seen:
                seen.add(url)
                images.append({"url": url, "file": file, "title": title})
                added += 1

# 多轮对话历史的独立预算（不从 MAX_CONTEXT_CHARS 切，避免影响 standalone 检索摘录数学）
MAX_HISTORY_CHARS = 6000
# 滑窗保留最近 N 轮（一轮 = 一对 Q+A）
HISTORY_WINDOW_TURNS = 3


async def _load_recent_turns(conversation_id: str, limit: int = HISTORY_WINDOW_TURNS, user_id: int | None = None) -> list[dict]:
    """按 conversation_id 取最近 limit 轮，返回由旧到新的 [{question, answer}]。
    user_id 非空时强制只取该用户的历史(防越权把他人会话注入上下文)。"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if user_id is not None:
            cursor = await db.execute(
                "SELECT question, answer FROM chat_history "
                "WHERE conversation_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
                (conversation_id, user_id, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT question, answer FROM chat_history "
                "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            )
        rows = await cursor.fetchall()
    turns = [{"question": r["question"], "answer": r["answer"]} for r in rows]
    turns.reverse()  # 由旧到新
    return turns


def _build_history_block(turns: list[dict]) -> str:
    """把最近若干轮格式化成对话历史文本块；空列表返回空串（回归安全，走未改分支）。

    超 MAX_HISTORY_CHARS 时先丢最旧轮次；单条最新轮仍超则截断答案。
    """
    if not turns:
        return ""
    blocks = []
    for t in turns:
        q = (t.get("question") or "").strip()
        a = (t.get("answer") or "").strip()
        blocks.append(f"用户：{q}\n保障负责人：{a}")
    # 先丢最旧轮次直到预算内
    while len(blocks) > 1 and len("\n\n".join(blocks)) > MAX_HISTORY_CHARS:
        blocks.pop(0)
    text = "\n\n".join(blocks)
    # 单条最新轮仍超预算 → 截断
    if len(text) > MAX_HISTORY_CHARS:
        text = text[:MAX_HISTORY_CHARS] + "…(截断)"
    return text


async def _tagged_stored_paths(tag_ids: list[int]) -> set[str]:
    """返回带有任一所选标签的文档 stored_path 集合（OR 语义，与文档管理页筛选一致）。

    tag_ids 为空时返回空集；调用方据此跳过过滤（零回归：不选标签时行为不变）。
    """
    if not tag_ids:
        return set()
    placeholders = ",".join("?" for _ in tag_ids)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"SELECT DISTINCT d.stored_path FROM documents d "
            f"JOIN document_tags dt ON d.id = dt.document_id "
            f"WHERE dt.tag_id IN ({placeholders})",
            tuple(tag_ids),
        )
        rows = await cursor.fetchall()
    return {r[0] for r in rows}


def _filter_results_by_paths(results: "SearchResults", allowed: set[str]) -> "SearchResults":
    """把 grep 结果裁剪到 allowed 路径集合内，保留 SearchResults 的排序元数据。"""
    filtered = SearchResults(r for r in results if r.file in allowed)
    filtered.original_keywords = getattr(results, "original_keywords", [])
    filtered.keyword_df = getattr(results, "keyword_df", {})
    return filtered


def _assemble_context(results: list, question: str) -> tuple[str, list[str], list[dict]]:
    """选文件(grep+IDF) → BM25-RRF 旁路融合 → 拼接上下文。
    返回 (search_text, files_to_read, relevant_images)。

    融合见 bm25.fuse_select：异常/不可用时等价于原 _select_files 顺序，零回归。
    被 BM25 捞回但无 grep 命中行的文件，读其开头(per_file_budget)以兑现召回增益。
    relevant_images：从真正进入上下文的 chunk 里抽的干净内容图（方案A 画廊用）。
    """
    baseline_files = _select_files(results)
    try:
        from bm25 import fuse_select
        files_to_read = fuse_select(baseline_files, question)
    except Exception:  # noqa: BLE001
        files_to_read = baseline_files
    if not files_to_read:
        return "未找到相关内容。", [], []

    parts = []
    total_chars = 0
    images: list[dict] = []
    seen_img: set[str] = set()
    per_file_budget = MAX_CONTEXT_CHARS // min(len(files_to_read), 10)
    for f in files_to_read:
        content = read_file_content(f)
        if not content:
            continue
        file_results = [r for r in results if r.file == f]
        if len(content) <= per_file_budget:
            chunk = f"=== 文件: {f} ===\n{content}\n"
        elif file_results:
            excerpts = _extract_relevant_sections(content, file_results, budget=per_file_budget)
            chunk = f"=== 文件: {f} (摘录) ===\n{excerpts}\n"
        else:
            # BM25 捞回但无 grep 命中行：读开头兜底，不丢召回增益
            chunk = f"=== 文件: {f} (摘录) ===\n{content[:per_file_budget]}\n"
        if total_chars + len(chunk) > MAX_CONTEXT_CHARS:
            break
        parts.append(chunk)
        total_chars += len(chunk)
        if len(images) < MAX_RELEVANT_IMAGES:
            # 从整篇正文抽图（而非摘录片段）：文件已被选为 top 相关，其内容图即相关图
            _collect_images_into(content, f, images, seen_img, MAX_IMAGES_PER_FILE)
    return "\n".join(parts), files_to_read, images[:MAX_RELEVANT_IMAGES]


@router.post("/query", response_model=QueryResponse)
async def query_knowledge_base(req: QueryRequest, user: dict = Depends(require_login)):
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
    if req.tag_ids:
        allowed = await _tagged_stored_paths(req.tag_ids)
        results = _filter_results_by_paths(results, allowed)
    t_search = time.perf_counter() - t0

    t0 = time.perf_counter()
    if results:
        search_text, files_to_read, relevant_images = _assemble_context(results, req.question)
    else:
        search_text, files_to_read, relevant_images = "未找到相关内容。", [], []
    t_extract = time.perf_counter() - t0

    try:
        t0 = time.perf_counter()
        answer_model, q_type = route_model(req.question, settings.llm_model, settings.llm_model_fast)
        answer, answer_llm_time = await generate_answer(req.question, search_text, model=answer_model)
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
        "answer_model": answer_model,
        "question_type": q_type,
        "search_results_count": len(results),
        "context_chars": len(search_text),
    }

    return QueryResponse(answer=answer, sources=sources, source_urls=source_urls, relevant_images=relevant_images, search_strategy=strategy, timing=timing)


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



def _table_span(sec_lines: list, idx: int) -> tuple:
    """若 sec_lines[idx] 在一个 Markdown 表格内（或紧邻），返回该表格的完整行范围 (start,end)。
    否则返回 None。表格是原子的——命中一行就应纳入整张表，避免数据被 ±radius 截断。"""
    def is_tbl(i):
        return 0 <= i < len(sec_lines) and sec_lines[i].lstrip().startswith("|")
    if not (is_tbl(idx) or is_tbl(idx - 1) or is_tbl(idx + 1)):
        return None
    # 以 idx 为锚，向上下扩展到连续表格行的边界
    anchor = idx if is_tbl(idx) else (idx - 1 if is_tbl(idx - 1) else idx + 1)
    s = anchor
    while is_tbl(s - 1):
        s -= 1
    e = anchor
    while is_tbl(e + 1):
        e += 1
    # 把表格上方一行（通常是表标题/说明）也带上
    s2 = s - 1 if s - 1 >= 0 and sec_lines[s - 1].strip() else s
    return (s2, e + 1)


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
            _sum_kws = ("统计", "总计", "汇总", "合计", "概览", "总结", "摘要") if _RETRIEVAL_OPT else ("统计", "总计", "汇总", "合计", "概览", "总结")
            is_summary = any(kw in sec_title for kw in _sum_kws)
            sections.append((sec_start, sec_end, sec_hits, is_summary))

    # Also include summary sections even without direct hits
    for idx in range(len(section_starts) - 1):
        sec_start = section_starts[idx]
        sec_end = section_starts[idx + 1]
        sec_title = lines[sec_start] if sec_start < total_lines else ""
        _sum_kws = ("统计", "总计", "汇总", "合计", "概览", "总结", "摘要") if _RETRIEVAL_OPT else ("统计", "总计", "汇总", "合计", "概览", "总结")
        if any(kw in sec_title for kw in _sum_kws):
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
            # 每个命中行命中了哪些不同关键词（用于按"关键词密度"排优先级）
            line_kws = {}
            for r in sec_hits:
                ln = r.line_number - 1 - sec_start
                line_kws.setdefault(ln, set()).add(getattr(r, "matched_keyword", "") or "")
            hit_indices = sorted(set(line_kws.keys()))
            ranges = []
            for hit in hit_indices:
                # 命中落在表格内 → 纳入整张表；否则取 ±context_radius
                tspan = _table_span(sec_lines, hit)
                if tspan:
                    s, e = tspan
                else:
                    s = max(0, hit - context_radius)
                    e = min(len(sec_lines), hit + context_radius + 1)
                if ranges and s <= ranges[-1][1]:
                    ranges[-1] = (ranges[-1][0], max(e, ranges[-1][1]))
                else:
                    ranges.append((s, e))

            # 预算紧张时按"覆盖的不同关键词数"降序优先（高密度命中行如表格摘要行先进上下文），
            # 取够预算后再按原文顺序还原，保证可读。
            def _range_score(rng):
                kws = set()
                for ln, ks in line_kws.items():
                    if rng[0] <= ln < rng[1]:
                        kws |= ks
                return len([k for k in kws if k])
            ordered = sorted(ranges, key=_range_score, reverse=True) if _RETRIEVAL_OPT else list(ranges)
            chosen = []
            sec_chars = 0
            for rng in ordered:
                snippet = "\n".join(sec_lines[rng[0]:rng[1]])
                if sec_chars + len(snippet) > budget_per_section:
                    if not chosen:  # 至少保一段（截断）
                        chosen.append((rng, snippet[:budget_per_section] + "\n...(截断)"))
                    continue
                chosen.append((rng, snippet))
                sec_chars += len(snippet)
            chosen.sort(key=lambda x: x[0][0])  # 还原文档顺序
            sec_parts = [c[1] for c in chosen]
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
async def simple_search(q: str, user: dict = Depends(require_login)):
    results = grep_search([q])
    return {
        "results": [
            {"file": r.file, "line": r.line_number, "content": r.content}
            for r in results[:30]
        ]
    }


@router.post("/query/stream")
async def query_knowledge_base_stream(req: QueryRequest, request: Request, user: dict = Depends(require_login)):
    settings = load_settings()
    if not settings.llm_api_key:
        raise HTTPException(400, "请先在设置中配置 LLM API Key")

    t_start = time.perf_counter()

    # 多轮上下文：仅当有 conversation_id 且能载入历史时才做指代消解（首轮/无会话零开销）
    recent_turns = []
    if req.conversation_id:
        # 绑定当前用户，防止他人会话历史被注入上下文(普通用户/管理员都只取自己的)
        recent_turns = await _load_recent_turns(req.conversation_id, user_id=user["id"])
    history_block = _build_history_block(recent_turns)

    if history_block:
        resolved_q, is_followup, coref_time = await resolve_coreference(history_block, req.question)
    else:
        resolved_q, is_followup, coref_time = req.question, False, 0.0

    try:
        strategy, strategy_llm_time = await generate_search_strategy(resolved_q)
    except RuntimeError as e:
        raise HTTPException(502, f"LLM 搜索策略生成失败: {e}")

    # 行为日志（尽力而为）：记录问答
    try:
        from activity import log_activity, current_user_brief, ACT_QUERY
        from auth import COOKIE_NAME
        _u = await current_user_brief(request.cookies.get(COOKIE_NAME))
        if _u:
            await log_activity(_u.get("id"), _u.get("username"), ACT_QUERY, req.question[:120])
    except Exception:
        pass

    keywords = strategy.get("keywords", [resolved_q])
    file_pattern = strategy.get("file_pattern", "*")

    t0 = time.perf_counter()
    results = grep_search(keywords, file_pattern)
    if req.tag_ids:
        allowed = await _tagged_stored_paths(req.tag_ids)
        results = _filter_results_by_paths(results, allowed)
    t_search = time.perf_counter() - t0

    if results:
        search_text, files_to_read, relevant_images = _assemble_context(results, resolved_q)
    else:
        search_text, files_to_read, relevant_images = "未找到相关内容。", [], []

    sources = [{"file": r.file, "line": r.line_number, "content": r.content} for r in results[:10]]
    source_urls = _extract_source_urls(files_to_read if results else [])
    t_prep = time.perf_counter() - t_start

    answer_model, q_type = route_model(resolved_q, settings.llm_model, settings.llm_model_fast)

    async def event_generator():
        meta = {
            "sources": sources,
            "source_urls": source_urls,
            "relevant_images": relevant_images,
            "search_strategy": strategy,
            "original_question": req.question,
            "resolved_question": resolved_q,
            "is_followup": is_followup,
            "answer_model": answer_model,
            "question_type": q_type,
            "timing_prep": round(t_prep, 2),
            "timing_coref": round(coref_time, 2),
            "timing_strategy": round(strategy_llm_time, 2),
            "timing_search": round(t_search, 2),
            "search_results_count": len(results),
            "context_chars": len(search_text),
        }
        yield f"data: {json.dumps({'type': 'meta', 'data': meta}, ensure_ascii=False)}\n\n"

        t0 = time.perf_counter()
        try:
            async for chunk in stream_answer(resolved_q, search_text, history_block=history_block, model=answer_model):
                yield f"data: {json.dumps({'type': 'chunk', 'data': chunk}, ensure_ascii=False)}\n\n"
        except RuntimeError as e:
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)}, ensure_ascii=False)}\n\n"
            return

        t_answer = time.perf_counter() - t0
        t_total = time.perf_counter() - t_start
        timing = {
            "total": round(t_total, 2),
            "coref": round(coref_time, 2),
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
    conversation_id: str | None = None
    selected_tags: list[dict] = []


@router.post("/chat/history")
async def save_chat_history(req: SaveHistoryRequest, user: dict = Depends(require_login)):
    """Save a Q&A pair to chat history (绑定当前登录用户)。"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chat_history (user_id, question, answer, source_urls, conversation_id, selected_tags, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                user["id"],
                req.question,
                req.answer,
                json.dumps(req.source_urls, ensure_ascii=False),
                req.conversation_id,
                json.dumps(req.selected_tags, ensure_ascii=False) if req.selected_tags else None,
                _now_bj(),
            ),
        )
        await db.commit()
    return {"message": "saved"}


@router.get("/chat/history")
async def get_chat_history(user_id: int | None = None, limit: int = 50, user: dict = Depends(require_login)):
    """Get chat history. 非管理员只能看自己的；管理员可传 user_id 或看全部。"""
    if user["role"] not in ("admin", "super"):
        user_id = user["id"]   # 普通用户强制只看本人，忽略客户端传入
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
                "selected_tags": _parse_tags(row["selected_tags"]),
                "created_at": row["created_at"],
                "user_name": row["user_name"] or "匿名",
            }
            for row in rows
        ]


@router.get("/chat/conversations")
async def list_conversations(user_id: int | None = None, limit: int = 50, user: dict = Depends(require_login)):
    """会话分组列表。非管理员只看本人；管理员可传 user_id 或看全部。"""
    if user["role"] not in ("admin", "super"):
        user_id = user["id"]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user_filter = "AND h.user_id = ?" if user_id else ""
        params: list = [user_id] if user_id else []

        # 1) 分组会话：取每个会话的最新轮 id + 轮数 + 是否用过标签
        cursor = await db.execute(
            f"""
            SELECT conversation_id, COUNT(*) AS turn_count,
                   MAX(id) AS last_id, MAX(created_at) AS last_time,
                   MAX(CASE WHEN selected_tags IS NOT NULL THEN 1 ELSE 0 END) AS has_tags
            FROM chat_history h
            WHERE conversation_id IS NOT NULL {user_filter}
            GROUP BY conversation_id
            """,
            params,
        )
        groups = await cursor.fetchall()

        items = []
        for g in groups:
            row_cur = await db.execute(
                "SELECT h.question, h.selected_tags, u.username AS user_name FROM chat_history h "
                "LEFT JOIN users u ON h.user_id = u.id WHERE h.id = ?",
                (g["last_id"],),
            )
            last = await row_cur.fetchone()
            items.append({
                "conversation_id": g["conversation_id"],
                "turn_count": g["turn_count"],
                "last_question": last["question"] if last else "",
                "created_at": g["last_time"],
                "user_name": (last["user_name"] if last else None) or "匿名",
                "has_tags": bool(g["has_tags"]),
                "selected_tags": _parse_tags(last["selected_tags"]) if last else [],
            })

        # 2) 历史 NULL 行：每条作为单轮伪会话
        legacy_cur = await db.execute(
            f"""
            SELECT h.id, h.question, h.created_at, h.selected_tags, u.username AS user_name
            FROM chat_history h LEFT JOIN users u ON h.user_id = u.id
            WHERE h.conversation_id IS NULL {user_filter}
            """,
            params,
        )
        for row in await legacy_cur.fetchall():
            tags = _parse_tags(row["selected_tags"])
            items.append({
                "conversation_id": f"legacy-{row['id']}",
                "turn_count": 1,
                "last_question": row["question"],
                "created_at": row["created_at"],
                "user_name": row["user_name"] or "匿名",
                "has_tags": bool(tags),
                "selected_tags": tags,
            })

    items.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return items[:limit]


@router.get("/chat/conversation/{conversation_id}")
async def get_conversation(conversation_id: str, user: dict = Depends(require_login)):
    """单会话全部轮次，按 id ASC（同秒不乱序）。legacy-{id} 为历史单轮伪会话。
    非管理员仅能读本人会话(防按 conversation_id/legacy-id 越权读他人内容)。"""
    own_only = user["role"] not in ("admin", "super")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if conversation_id.startswith("legacy-"):
            try:
                row_id = int(conversation_id.removeprefix("legacy-"))
            except ValueError:
                return []
            if own_only:
                cursor = await db.execute(
                    "SELECT h.*, u.username AS user_name FROM chat_history h "
                    "LEFT JOIN users u ON h.user_id = u.id WHERE h.id = ? AND h.user_id = ?",
                    (row_id, user["id"]),
                )
            else:
                cursor = await db.execute(
                    "SELECT h.*, u.username AS user_name FROM chat_history h "
                    "LEFT JOIN users u ON h.user_id = u.id WHERE h.id = ?",
                    (row_id,),
                )
        else:
            if own_only:
                cursor = await db.execute(
                    "SELECT h.*, u.username AS user_name FROM chat_history h "
                    "LEFT JOIN users u ON h.user_id = u.id WHERE h.conversation_id = ? AND h.user_id = ? ORDER BY h.id ASC",
                    (conversation_id, user["id"]),
                )
            else:
                cursor = await db.execute(
                    "SELECT h.*, u.username AS user_name FROM chat_history h "
                    "LEFT JOIN users u ON h.user_id = u.id WHERE h.conversation_id = ? ORDER BY h.id ASC",
                    (conversation_id,),
                )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "question": row["question"],
                "answer": row["answer"],
                "source_urls": json.loads(row["source_urls"]) if row["source_urls"] else [],
                "selected_tags": _parse_tags(row["selected_tags"]),
                "created_at": row["created_at"],
                "user_name": row["user_name"] or "匿名",
            }
            for row in rows
        ]


@router.delete("/chat/history/{history_id}")
async def delete_chat_history(history_id: int, user: dict = Depends(require_login)):
    async with aiosqlite.connect(DB_PATH) as db:
        # 非管理员只能删自己的记录
        if user["role"] not in ("admin", "super"):
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT user_id FROM chat_history WHERE id = ?", (history_id,))).fetchone()
            if not row:
                raise HTTPException(404, "记录不存在")
            if row["user_id"] != user["id"]:
                raise HTTPException(403, "无权删除他人记录")
        await db.execute("DELETE FROM chat_history WHERE id = ?", (history_id,))
        await db.commit()
    return {"message": "deleted"}
