"""作战全局地图（认知地图）：基于知识库文档 + 主题标签方向，用 LLM 归纳每个方向的
全局认知卡片（定位/关键系统/历史事故/水深点/推荐文档），帮助新负责人快速建立大局观。

与保障清单完全解耦：数据源是知识库文档，不是清单条目；产物是认知，不是 TODO。
后台生成 + 缓存（battle_map 表，每方向一行），全体只读，管理员可重新生成。
"""
import os
import json
import re
import asyncio
import aiosqlite
from fastapi import APIRouter, Request, HTTPException

from database import DB_PATH
from config import load_settings
from searcher import read_file_content
from auth import COOKIE_NAME
import llm

router = APIRouter(tags=["battlemap"])

# 维度定义来自单一事实源 dimensions.py
from dimensions import DIMENSION_TAGS as DIMENSIONS, TAG_TO_LABEL as DIM_LABEL

PER_DOC_LIMIT = 3500     # 单篇喂给"要点抽取"的正文上限
MAX_DOCS_PER_DIM = 0     # 0 = 全量扫描该方向所有文档（质量优先，不截断；第一段逐篇并发不受LLM上下文限制）
SCAN_CONCURRENCY = 6     # 全量下适度提高并发以缩短总时长

_PROGRESS: dict = {"status": "idle", "total": 0, "done": 0, "current": ""}


def _now():
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


_SRC_RE = re.compile(r"^>\s*来源[:：]\s*(\S+)", re.M)


def _source_url(kb: str, stored_path: str) -> str:
    """从 md 文件开头的 `> 来源:` 提取原始文档链接（企微/info 均可），点击直达原文。"""
    try:
        head = open(os.path.join(kb, stored_path), encoding="utf-8", errors="replace").read(800)
        m = _SRC_RE.search(head)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _model():
    return load_settings().llm_model


POINT_SYS = """你在为"大型直播活动保障"知识库做某个保障方向的要点提炼。给你一篇文档，请只提取与【{dim}】方向相关、
对新任保障负责人建立认知有价值的要点：涉及的关键系统/链路/技术环节、发生过的事故或教训、容易踩的坑。
若该文档与本方向无关或无有价值信息，回复空。简洁分条，不超过 5 条，每条一句话。只输出要点，不要解释。"""

CARD_SYS = """你在为"大型直播活动保障"知识库生成【{dim}】方向的"认知地图卡片"，读者是刚接手的新保障负责人，
目的是让他快速建立"这块是什么、涉及哪些系统、历史踩过哪些坑、哪里水深"的全局认知（不是待办清单）。
下面是从该方向多篇文档提炼的要点汇总。请归纳为严格 JSON：
{{
  "positioning": "一句话定位这个保障方向是什么",
  "key_systems": ["关键系统/链路/技术环节", ...],
  "history": ["历史上发生过的标志性事故/教训(具体)", ...],
  "pitfalls": ["新人最该警惕的坑/易踩点", ...]
}}
要求：基于给定要点归纳、不编造；各数组 3-8 条；只输出 JSON。"""


async def _extract_points(name: str, content: str, dim: str, sem) -> str:
    async with sem:
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": POINT_SYS.format(dim=dim)},
                 {"role": "user", "content": f"文档标题：{name}\n\n正文：\n{content[:PER_DOC_LIMIT]}"}],
                temperature=0, model=_model())
            return txt.strip()
        except Exception:
            return ""


async def _docs_of_dim(dim: str) -> list:
    """取该方向标签下的文档。MAX_DOCS_PER_DIM=0 时全量取（质量优先）。"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        sql = """SELECT DISTINCT d.original_name, d.stored_path
                 FROM documents d JOIN document_tags dt ON d.id = dt.document_id
                 JOIN tags t ON t.id = dt.tag_id WHERE t.name = ?"""
        params = [dim]
        if MAX_DOCS_PER_DIM and MAX_DOCS_PER_DIM > 0:
            sql += " LIMIT ?"
            params.append(MAX_DOCS_PER_DIM)
        rows = await (await db.execute(sql, params)).fetchall()
        return [dict(r) for r in rows]


async def _gen_one_dim(dim: str, kb: str, sem) -> dict:
    """生成单个方向的认知卡片，返回 {dimension, content, source_doc_count}。"""
    docs = await _docs_of_dim(dim)
    # 两段式：① 并发抽每篇要点 ② 汇总成卡片
    point_tasks = []
    for d in docs:
        path = os.path.join(kb, d["stored_path"])
        content = read_file_content(d["stored_path"]) if os.path.exists(path) else ""
        if content:
            point_tasks.append(_extract_points(d["original_name"], content, dim, sem))
    points = await asyncio.gather(*point_tasks) if point_tasks else []
    points = [p for p in points if p]
    # 推荐文档：取文档列表前若干（有内容的），给前端可点开
    rec_docs = [{"title": d["original_name"], "path": d["stored_path"], "url": _source_url(kb, d["stored_path"])} for d in docs[:8]]

    card = {"positioning": "", "key_systems": [], "history": [], "pitfalls": [], "recommended_docs": rec_docs}
    if points:
        # 分层归并：要点过多时先分批压缩，避免一次汇总把数据截断丢失（全量质量优先）
        merged = await _reduce_points(points, dim)
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": CARD_SYS.format(dim=DIM_LABEL.get(dim, dim))},
                 {"role": "user", "content": merged}],
                temperature=0, model=_model())
            txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(txt)
            for k in ("positioning", "key_systems", "history", "pitfalls"):
                if k in parsed:
                    card[k] = parsed[k]
        except Exception:
            pass
    return {"dimension": dim, "content": card, "source_doc_count": len(docs)}


REDUCE_SYS = """下面是某保障方向从多篇文档抽取的要点（可能很多）。请在不丢失关键信息的前提下做归并去重：
合并语义重复项、保留所有不同的关键系统/事故/坑，输出更精炼的要点列表（分条，每条一句）。只输出要点。"""


async def _reduce_points(points: list, dim: str, budget: int = 20000) -> str:
    """要点总量超预算时，分批用 LLM 压缩归并到一层，循环直到放得下——避免简单截断丢数据。"""
    joined = "\n".join(f"- {p}" for p in points)
    if len(joined) <= budget:
        return joined
    # 分批压缩
    batches, cur, cur_len = [], [], 0
    for p in points:
        if cur_len + len(p) > budget and cur:
            batches.append(cur); cur, cur_len = [], 0
        cur.append(p); cur_len += len(p) + 3
    if cur:
        batches.append(cur)
    reduced = []
    for b in batches:
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": REDUCE_SYS},
                 {"role": "user", "content": "\n".join(f"- {x}" for x in b)}],
                temperature=0, model=_model())
            reduced.append(txt.strip())
        except Exception:
            reduced.append("\n".join(f"- {x}" for x in b[:30]))  # 压缩失败兜底保留部分
    out = "\n".join(reduced)
    # 若压缩一轮后仍超预算，递归再压
    if len(out) > budget:
        return await _reduce_points([l.strip("- ").strip() for l in out.splitlines() if l.strip()], dim, budget)
    return out


async def _genmap_task(generated_by: str):
    """后台逐方向生成。进度写入 _PROGRESS，供轮询与微信汇报读取。"""
    kb = load_settings().knowledge_base_dir
    sem = asyncio.Semaphore(SCAN_CONCURRENCY)
    _PROGRESS.update({"status": "generating", "total": len(DIMENSIONS), "done": 0, "current": ""})
    try:
        for dim in DIMENSIONS:
            _PROGRESS["current"] = dim
            res = await _gen_one_dim(dim, kb, sem)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT INTO battle_map (dimension, content_json, source_doc_count, status, generated_by, updated_at)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(dimension) DO UPDATE SET
                         content_json=excluded.content_json, source_doc_count=excluded.source_doc_count,
                         status='done', generated_by=excluded.generated_by, updated_at=excluded.updated_at""",
                    (dim, json.dumps(res["content"], ensure_ascii=False), res["source_doc_count"], "done", generated_by, _now()))
                await db.commit()
            _PROGRESS["done"] += 1
        _PROGRESS["status"] = "done"
    except Exception as e:  # noqa: BLE001
        _PROGRESS["status"] = "failed"
        _PROGRESS["error"] = str(e)[:200]


async def _require_admin(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT username, role FROM users WHERE token = ?", (token,))).fetchone()
    if not row or row["role"] != "admin":
        raise HTTPException(403, "仅管理员可生成作战地图")
    return {"username": row["username"]}


@router.get("/battlemap")
async def get_battlemap():
    """读取 6 方向最新卡片（全体只读）。"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM battle_map")).fetchall()
    by_dim = {r["dimension"]: r for r in rows}
    out = []
    for dim in DIMENSIONS:
        r = by_dim.get(dim)
        out.append({
            "dimension": dim,
            "label": DIM_LABEL.get(dim, dim),
            "content": json.loads(r["content_json"]) if r and r["content_json"] else None,
            "source_doc_count": r["source_doc_count"] if r else 0,
            "updated_at": r["updated_at"] if r else None,
            "generated_by": r["generated_by"] if r else None,
        })
    # 关键角色与团队（展示在方向卡片前）
    roles = None
    rr = by_dim.get("__roles__")
    if rr and rr["content_json"]:
        try:
            roles = json.loads(rr["content_json"]).get("roles")
        except Exception:
            roles = None
    # 历史大型活动（时间倒序，展示在最前）
    events = None
    er = by_dim.get("__events__")
    if er and er["content_json"]:
        try:
            events = json.loads(er["content_json"]).get("events")
        except Exception:
            events = None

    def _extra(key, field):
        r = by_dim.get(key)
        if r and r["content_json"]:
            try:
                return json.loads(r["content_json"]).get(field)
            except Exception:
                return None
        return None
    baseline = _extra("__baseline__", "baseline")
    timeline = _extra("__timeline__", "timeline")
    return {"dimensions": out, "events": events, "baseline": baseline,
            "timeline": timeline, "roles": roles, "progress": _PROGRESS}

@router.post("/battlemap/generate")
async def generate_battlemap(request: Request):
    me = await _require_admin(request)
    if _PROGRESS.get("status") == "generating":
        raise HTTPException(409, "作战地图正在生成中，请稍候")
    asyncio.create_task(_genmap_task(me["username"]))
    return {"status": "generating", "total": len(DIMENSIONS)}


@router.get("/battlemap/progress")
async def battlemap_progress():
    return _PROGRESS
