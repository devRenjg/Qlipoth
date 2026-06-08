"""保障清单（历史踩坑预警）：扫某活动的历史复盘文档，LLM 按六维度抽取踩坑，
落库成一份可编辑、可跟踪的备战清单。

L1 + B方案：第一版只聚焦复盘类文档（文件名含 复盘/总结/问题/故障 + 活动标签）。
两阶段抽取：① 每篇复盘 LLM 抽踩坑条目 ② 汇总归并落库。生成为后台任务，前端轮询进度。
"""
import asyncio
import json
import os
from datetime import datetime, timezone, timedelta

import aiosqlite
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import llm
from config import load_settings
from database import DB_PATH
from auth import COOKIE_NAME
from searcher import read_file_content

router = APIRouter(tags=["checklist"])
_BJ = timezone(timedelta(hours=8))

ACTIVITIES = ["S赛", "跨晚", "春晚"]
DIMENSIONS = ["高可用保障", "直播播放体验", "安全", "业务需求", "成本", "可复用沉淀"]
# 保障周期阶段（按发生先后排序，先发生在前）
STAGES = ["备战前期", "压测演练", "上线前", "活动当天", "活动后", "未分类"]
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}
REVIEW_KEYWORDS = ("复盘", "总结", "问题", "故障")
CONTENT_LIMIT = 8000

# 生成任务的内存进度（checklist 生成是低频长任务，前端轮询）
_GEN_PROGRESS: dict = {}


def _now():
    return datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")


EXTRACT_SYS = f"""你在为"大型直播活动保障"团队，从一篇**历史活动复盘文档**中提炼"踩过的坑/教训"，
帮助下一届活动备战时主动规避。请严格按保障的六大维度归类每一条踩坑：
- 高可用保障：能看能播、PCU、降级、限流、容灾、架构、压测
- 直播播放体验：卡顿、延迟、清晰度、首帧、画质
- 安全：审核、风控、弹幕真实性、黑灰产、冲塔
- 业务需求：增长目标相关的功能/玩法需求层面的问题
- 成本：带宽、采购、服务器预算（注意采购是否需要更早前置介入）
- 可复用沉淀：流程、工具、机制层面可沉淀复用的经验

只提炼文档中**确实发生过的问题/故障/教训/改进项**，不要编造、不要把"做得好的地方"当坑。
每条尽量具体、可落地。若文档没有明显踩坑内容，返回空数组。

另外为每条标注：
- stage 保障周期阶段(这个坑该在哪个阶段防范/发生在哪个阶段)，从这些里选一个：备战前期 / 压测演练 / 上线前 / 活动当天 / 活动后；判断不出填"未分类"
- team 负责团队、owner 负责人：仅当文档中明确写明该坑归属的团队或 @负责人时填写，**不确定就留空字符串**，不要猜

返回严格 JSON（不要 markdown）：
{{"items": [
  {{"dimension": "高可用保障", "stage": "压测演练", "team": "", "owner": "", "phenomenon": "现象(发生了什么)", "cause": "原因", "handling": "当时如何处置/解决", "suggestion": "给下一届的建议", "timing": "该提前到什么时点做(没有则空串)"}}
]}}
dimension 必须逐字取自六维度之一，stage 必须取自上面五个阶段或"未分类"。"""


def _review_docs_sql():
    like = " OR ".join(["d.original_name LIKE ?"] * len(REVIEW_KEYWORDS))
    return (
        f"""SELECT DISTINCT d.id, d.original_name, d.stored_path FROM documents d
            JOIN document_tags dt ON d.id = dt.document_id
            JOIN tags t ON t.id = dt.tag_id
            WHERE t.name = ? AND ({like})""",
        [f"%{k}%" for k in REVIEW_KEYWORDS],
    )


async def _require_admin(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT role FROM users WHERE token = ?", (token,))
        caller = await cur.fetchone()
        if not caller or caller["role"] != "admin":
            raise HTTPException(403, "无权限")


async def _extract_from_doc(name: str, content: str, sem: asyncio.Semaphore) -> list[dict]:
    """对一篇复盘文档抽取踩坑条目。失败返回空（best-effort）。"""
    async with sem:
        user = f"复盘文档标题：{name}\n\n复盘文档内容：\n{content[:CONTENT_LIMIT]}"
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": EXTRACT_SYS}, {"role": "user", "content": user}],
                temperature=0, model=load_settings().llm_model)
        except Exception:  # noqa: BLE001
            return []
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        items = json.loads(txt).get("items", [])
    except json.JSONDecodeError:
        return []
    out = []
    for it in items:
        dim = it.get("dimension", "")
        if dim not in DIMENSIONS:
            continue
        stage = it.get("stage", "未分类")
        if stage not in STAGES:
            stage = "未分类"
        out.append({
            "dimension": dim,
            "stage": stage,
            "team": (it.get("team") or "").strip(),
            "owner": (it.get("owner") or "").strip(),
            "phenomenon": (it.get("phenomenon") or "").strip(),
            "cause": (it.get("cause") or "").strip(),
            "handling": (it.get("handling") or "").strip(),
            "suggestion": (it.get("suggestion") or "").strip(),
            "timing": (it.get("timing") or "").strip(),
            "source_file": name,
        })
    return out


async def _generate_task(activity: str, checklist_id: int):
    """后台生成任务：扫复盘文档 → 并发抽取 → 落库 items。进度写入 _GEN_PROGRESS。"""
    prog = _GEN_PROGRESS[checklist_id]
    try:
        sql, params = _review_docs_sql()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, [activity] + params)
            docs = await cur.fetchall()
        prog["total"] = len(docs)
        kb = load_settings().knowledge_base_dir

        sem = asyncio.Semaphore(4)
        done = {"n": 0}

        async def _one(d):
            path = os.path.join(kb, d["stored_path"])
            content = read_file_content(d["stored_path"]) if os.path.exists(path) else ""
            items = await _extract_from_doc(d["original_name"], content, sem) if content else []
            done["n"] += 1
            prog["processed"] = done["n"]
            return items

        results = await asyncio.gather(*[_one(d) for d in docs])
        all_items = [it for r in results for it in r]

        # 按六维度、再按保障阶段(先发生在前)排序
        dim_order = {d: i for i, d in enumerate(DIMENSIONS)}
        all_items.sort(key=lambda x: (dim_order.get(x["dimension"], 99),
                                       STAGE_ORDER.get(x.get("stage", "未分类"), 99)))

        async with aiosqlite.connect(DB_PATH) as db:
            for i, it in enumerate(all_items):
                await db.execute(
                    """INSERT INTO checklist_items
                       (checklist_id, dimension, stage, team, owner, phenomenon, cause, handling, suggestion, timing, source_files, sort_order)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (checklist_id, it["dimension"], it["stage"], it["team"], it["owner"],
                     it["phenomenon"], it["cause"], it["handling"], it["suggestion"],
                     it["timing"], it["source_file"], i))
            await db.execute(
                "UPDATE checklists SET source_doc_count = ?, status = 'active' WHERE id = ?",
                (len(docs), checklist_id))
            await db.commit()
        prog["status"] = "done"
        prog["item_count"] = len(all_items)
    except Exception as e:  # noqa: BLE001
        prog["status"] = "error"
        prog["error"] = str(e)


class GenerateReq(BaseModel):
    activity: str
    title: str | None = None


@router.post("/checklist/generate")
async def generate_checklist(req: GenerateReq, request: Request):
    await _require_admin(request)
    if req.activity not in ACTIVITIES:
        raise HTTPException(400, f"activity 必须是 {ACTIVITIES} 之一")
    title = req.title or f"{req.activity} 备战踩坑清单"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO checklists (activity, title, status, created_at) VALUES (?,?,?,?)",
            (req.activity, title, "generating", _now()))
        await db.commit()
        checklist_id = cur.lastrowid
    _GEN_PROGRESS[checklist_id] = {"status": "generating", "total": 0, "processed": 0}
    asyncio.create_task(_generate_task(req.activity, checklist_id))
    return {"id": checklist_id, "title": title, "status": "generating"}


@router.get("/checklist/generate/{checklist_id}/progress")
async def generate_progress(checklist_id: int, request: Request):
    await _require_admin(request)
    prog = _GEN_PROGRESS.get(checklist_id)
    if not prog:
        # 进程重启后内存丢失：看库里状态
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT status FROM checklists WHERE id = ?", (checklist_id,))
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "清单不存在")
        return {"status": row["status"]}
    return prog


@router.get("/checklist/list")
async def list_checklists(request: Request):
    await _require_admin(request)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT c.*, (SELECT COUNT(*) FROM checklist_items WHERE checklist_id = c.id) AS item_count,
                      (SELECT COUNT(*) FROM checklist_items WHERE checklist_id = c.id AND handled = 1) AS handled_count
               FROM checklists c ORDER BY c.created_at DESC""")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


@router.get("/checklist/{checklist_id}")
async def get_checklist(checklist_id: int, request: Request):
    await _require_admin(request)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM checklists WHERE id = ?", (checklist_id,))
        cl = await cur.fetchone()
        if not cl:
            raise HTTPException(404, "清单不存在")
        cur = await db.execute(
            "SELECT * FROM checklist_items WHERE checklist_id = ? ORDER BY sort_order, id",
            (checklist_id,))
        items = [dict(r) for r in await cur.fetchall()]
    # 按维度分组，组内按保障阶段(先发生在前)排序
    grouped = {d: [] for d in DIMENSIONS}
    for it in items:
        grouped.setdefault(it["dimension"], []).append(it)
    for d in grouped:
        grouped[d].sort(key=lambda x: (STAGE_ORDER.get(x.get("stage") or "未分类", 99),
                                       x.get("sort_order", 0)))
    return {"checklist": dict(cl), "dimensions": DIMENSIONS, "stages": STAGES,
            "grouped": grouped, "item_count": len(items),
            "handled_count": sum(1 for it in items if it["handled"])}


class ItemUpdate(BaseModel):
    dimension: str | None = None
    stage: str | None = None
    team: str | None = None
    owner: str | None = None
    phenomenon: str | None = None
    cause: str | None = None
    handling: str | None = None
    suggestion: str | None = None
    timing: str | None = None
    handled: bool | None = None


async def _username(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return ""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT username FROM users WHERE token = ?", (token,))
        row = await cur.fetchone()
        return row["username"] if row else ""


@router.patch("/checklist/item/{item_id}")
async def update_item(item_id: int, req: ItemUpdate, request: Request):
    await _require_admin(request)
    fields, vals = [], []
    for k in ("dimension", "stage", "team", "owner", "phenomenon", "cause", "handling", "suggestion", "timing"):
        v = getattr(req, k)
        if v is not None:
            fields.append(f"{k} = ?")
            vals.append(v)
    if req.handled is not None:
        fields.append("handled = ?")
        vals.append(1 if req.handled else 0)
        # 勾选追溯：记录谁、何时
        if req.handled:
            who = await _username(request)
            fields += ["handled_by = ?", "handled_at = ?"]
            vals += [who, _now()]
        else:
            fields += ["handled_by = ?", "handled_at = ?"]
            vals += ["", ""]
    if not fields:
        return {"message": "无更新"}
    vals.append(item_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE checklist_items SET {', '.join(fields)} WHERE id = ?", vals)
        await db.commit()
    return {"message": "已更新"}


class ItemCreate(BaseModel):
    dimension: str
    stage: str = "未分类"
    team: str = ""
    owner: str = ""
    phenomenon: str = ""
    cause: str = ""
    handling: str = ""
    suggestion: str = ""
    timing: str = ""


@router.post("/checklist/{checklist_id}/item")
async def add_item(checklist_id: int, req: ItemCreate, request: Request):
    await _require_admin(request)
    if req.dimension not in DIMENSIONS:
        raise HTTPException(400, f"dimension 必须是六维度之一")
    stage = req.stage if req.stage in STAGES else "未分类"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT MAX(sort_order) FROM checklist_items WHERE checklist_id = ?",
                               (checklist_id,))
        mx = (await cur.fetchone())[0] or 0
        cur = await db.execute(
            """INSERT INTO checklist_items
               (checklist_id, dimension, stage, team, owner, phenomenon, cause, handling, suggestion, timing, source_files, sort_order)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (checklist_id, req.dimension, stage, req.team, req.owner, req.phenomenon,
             req.cause, req.handling, req.suggestion, req.timing, "(手动添加)", mx + 1))
        await db.commit()
        return {"id": cur.lastrowid, "message": "已添加"}


@router.delete("/checklist/item/{item_id}")
async def delete_item(item_id: int, request: Request):
    await _require_admin(request)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM checklist_items WHERE id = ?", (item_id,))
        await db.commit()
    return {"message": "已删除"}


@router.delete("/checklist/{checklist_id}")
async def delete_checklist(checklist_id: int, request: Request):
    await _require_admin(request)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM checklist_items WHERE checklist_id = ?", (checklist_id,))
        await db.execute("DELETE FROM checklists WHERE id = ?", (checklist_id,))
        await db.commit()
    _GEN_PROGRESS.pop(checklist_id, None)
    return {"message": "已删除"}
