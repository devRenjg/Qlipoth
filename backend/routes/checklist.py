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
# 事故/故障置顶警醒；撤掉"可复用沉淀"(太虚，内容并入其余维度)
DIMENSIONS = ["事故/故障", "高可用保障", "直播播放体验", "安全", "业务需求", "成本"]
# 严重度分级
SEVERITIES = ["P0", "P1", "P2"]
# 保障周期阶段（按发生先后排序，先发生在前）
STAGES = ["备战前期", "压测演练", "上线前", "活动当天", "活动后", "未分类"]
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}
REVIEW_KEYWORDS = ("复盘", "总结", "问题", "故障")
CONTENT_LIMIT = 8000
CROSS_PER_DIM = 10  # 跨活动借鉴每维度最多保留条数，防止灌水淹没本活动

# 生成任务的内存进度（checklist 生成是低频长任务，前端轮询）
_GEN_PROGRESS: dict = {}


def _now():
    return datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")


EXTRACT_SYS = f"""你在为"大型直播活动保障"团队，从一篇历史活动文档中提炼"踩过的坑/教训/事故"，
帮助下一届活动备战时主动规避。请把每一条归到下面六个维度之一：
- 事故/故障：发生过的重大事故、资损、严重故障、影响用户的线上问题（最高优先级，凡"出过事"的归这里）
- 高可用保障：能看能播、PCU、降级、限流、容灾、架构、压测；流程/工具/机制类技术沉淀也归这里
- 直播播放体验：卡顿、延迟、清晰度、首帧、画质
- 安全：弹幕安全(审核/真实性/黑灰产/冲塔)、业务安全(风控/作弊/资损防护)、基础技术安全(攻击/防护)——非常重要，凡涉及安全的务必归此维度，不可漏
- 业务需求：增长目标相关的功能/玩法需求层面的问题；运营/排期/项目协调类也归这里
- 成本：带宽、采购、服务器预算（注意采购是否需要更早前置介入）

只提炼文档中**确实发生过的问题/故障/事故/教训/改进项**，不要编造、不要把"做得好的地方"或
"纯方案设想"当坑。若文档是纯方案/需求/名单类、没有实际踩坑内容，返回空数组。

为每条标注：
- severity 严重度：P0(重大事故/资损/严重故障) / P1(重要，需重点关注) / P2(一般改进项)
- stage 保障周期阶段：备战前期 / 压测演练 / 上线前 / 活动当天 / 活动后；判不出填"未分类"
- team 负责团队/业务线（流媒体、基架、弹幕、OTT/TV端、产品、研发、质量保障、直播体验、带宽/成本、安全 等），尽量填
- owner 负责人：仅当出现**真实中文姓名**时填，脱敏ID(@eJP3类)、字体名、纯数字一律留空
- cross_activity 是否跨活动通用：对其他大型活动(春晚/跨晚/S赛)也有借鉴价值则 true，纯本活动特有 false

返回严格 JSON（不要 markdown）：
{{"items": [
  {{"dimension": "事故/故障", "severity": "P0", "stage": "活动当天", "team": "流媒体", "owner": "", "cross_activity": true, "phenomenon": "现象", "cause": "原因", "handling": "当时处置", "suggestion": "给下一届的建议", "timing": "该提前到的时点(无则空串)"}}
]}}
dimension 必须逐字取自六个维度之一，severity 取 P0/P1/P2，stage 取五个阶段或"未分类"。"""


def _clean_owner(name: str) -> str:
    """只保留真实中文姓名，剔除脱敏ID/字体名/纯数字/乱码。"""
    import re
    name = (name or "").strip().lstrip("@")
    if not name or re.search(r"[A-Za-z0-9]", name) or name in ("微软雅黑", "宋体", "黑体"):
        return ""
    if not re.search(r"[一-鿿]", name) or len(name) > 8:
        return ""
    return name


def _review_docs_sql():
    """复盘类文档 + 带"安全"主题标签的文档（安全很重要，不只看复盘）。"""
    like = " OR ".join(["d.original_name LIKE ?"] * len(REVIEW_KEYWORDS))
    return (
        f"""SELECT DISTINCT d.id, d.original_name, d.stored_path FROM documents d
            JOIN document_tags dt ON d.id = dt.document_id
            JOIN tags t ON t.id = dt.tag_id
            WHERE t.name = ? AND (
                ({like})
                OR d.id IN (
                    SELECT dt2.document_id FROM document_tags dt2
                    JOIN tags t2 ON t2.id = dt2.tag_id WHERE t2.name = '安全'
                )
            )""",
        [f"%{k}%" for k in REVIEW_KEYWORDS],
    )


def _all_docs_sql():
    """某活动下全部文档（全文档扫描）。"""
    return (
        """SELECT DISTINCT d.id, d.original_name, d.stored_path FROM documents d
           JOIN document_tags dt ON d.id = dt.document_id
           JOIN tags t ON t.id = dt.tag_id
           WHERE t.name = ?""",
        [],
    )


async def _require_login(request: Request):
    """保障清单对所有登录用户开放（admin/super/user 均可查看与生成）。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM users WHERE token = ?", (token,))
        if not await cur.fetchone():
            raise HTTPException(401, "登录已失效")


async def _extract_from_doc(name: str, content: str, sem: asyncio.Semaphore) -> list[dict]:
    """对一篇文档抽取踩坑条目。失败返回空（best-effort）。"""
    async with sem:
        user = f"文档标题：{name}\n\n文档内容：\n{content[:CONTENT_LIMIT]}"
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
        sev = it.get("severity", "P2")
        if sev not in SEVERITIES:
            sev = "P2"
        out.append({
            "dimension": dim,
            "severity": sev,
            "stage": stage,
            "team": (it.get("team") or "").strip(),
            "owner": _clean_owner(it.get("owner")),
            "cross_activity": bool(it.get("cross_activity")),
            "phenomenon": (it.get("phenomenon") or "").strip(),
            "cause": (it.get("cause") or "").strip(),
            "handling": (it.get("handling") or "").strip(),
            "suggestion": (it.get("suggestion") or "").strip(),
            "timing": (it.get("timing") or "").strip(),
            "source_file": name,
        })
    return out


async def _scan_docs(docs, kb, sem, prog, total):
    """并发扫一批文档抽取条目，更新进度。"""
    done = {"n": prog.get("processed", 0)}

    async def _one(d):
        path = os.path.join(kb, d["stored_path"])
        content = read_file_content(d["stored_path"]) if os.path.exists(path) else ""
        items = await _extract_from_doc(d["original_name"], content, sem) if content else []
        done["n"] += 1
        prog["processed"] = done["n"]
        return items

    results = await asyncio.gather(*[_one(d) for d in docs])
    return [it for r in results for it in r]


async def _generate_task(activity: str, checklist_id: int):
    """后台生成：①全文档扫本活动 ②从其他活动复盘取跨活动通用坑(借鉴)→ 落库。"""
    prog = _GEN_PROGRESS[checklist_id]
    try:
        kb = load_settings().knowledge_base_dir
        sem = asyncio.Semaphore(4)

        # ① 本活动全文档扫描
        all_sql, _ = _all_docs_sql()
        rev_sql, rev_p = _review_docs_sql()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            own_docs = await (await db.execute(all_sql, [activity])).fetchall()
            # ② 其他活动的复盘文档（只取通用坑借鉴）
            others = [a for a in ACTIVITIES if a != activity]
            cross_docs = {}
            for oa in others:
                rows = await (await db.execute(rev_sql, [oa] + rev_p)).fetchall()
                cross_docs[oa] = rows
        prog["total"] = len(own_docs) + sum(len(v) for v in cross_docs.values())

        own_items = await _scan_docs(own_docs, kb, sem, prog, prog["total"])
        for it in own_items:
            it["cross_from"] = ""

        # 跨活动借鉴：只保留 cross_activity=true，去重 + 每维度限额，避免灌水淹没本活动
        cross_raw = []
        for oa, rows in cross_docs.items():
            items = await _scan_docs(rows, kb, sem, prog, prog["total"])
            for it in items:
                if it.get("cross_activity"):
                    it["cross_from"] = oa
                    cross_raw.append(it)

        seen = {(it["phenomenon"] or "")[:20] for it in own_items}
        per_dim: dict = {}
        cross_items = []
        for it in cross_raw:
            key = (it["phenomenon"] or "")[:20]
            if key in seen:
                continue
            n = per_dim.get(it["dimension"], 0)
            if n >= CROSS_PER_DIM:
                continue
            seen.add(key)
            per_dim[it["dimension"]] = n + 1
            cross_items.append(it)

        all_items = own_items + cross_items

        # 排序：维度(事故置顶) → 严重度(P0先) → 保障阶段(先发生在前)
        dim_order = {d: i for i, d in enumerate(DIMENSIONS)}
        sev_order = {s: i for i, s in enumerate(SEVERITIES)}
        all_items.sort(key=lambda x: (dim_order.get(x["dimension"], 99),
                                       sev_order.get(x.get("severity", "P2"), 9),
                                       STAGE_ORDER.get(x.get("stage", "未分类"), 99)))

        async with aiosqlite.connect(DB_PATH) as db:
            for i, it in enumerate(all_items):
                await db.execute(
                    """INSERT INTO checklist_items
                       (checklist_id, dimension, severity, stage, team, owner, cross_from, phenomenon, cause, handling, suggestion, timing, source_files, sort_order)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (checklist_id, it["dimension"], it.get("severity", "P2"), it["stage"], it["team"], it["owner"],
                     it.get("cross_from", ""), it["phenomenon"], it["cause"], it["handling"],
                     it["suggestion"], it["timing"], it["source_file"], i))
            await db.execute(
                "UPDATE checklists SET source_doc_count = ?, status = 'active' WHERE id = ?",
                (len(own_docs), checklist_id))
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
    await _require_login(request)
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
    await _require_login(request)
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
    await _require_login(request)
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
    await _require_login(request)
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
        # 来源文档标题 → 原始企业微信链接(source_url) 反查
        url_map = {}
        for r in await (await db.execute("SELECT original_name, source_url FROM documents")).fetchall():
            if r["source_url"]:
                url_map[r["original_name"]] = r["source_url"]
    for it in items:
        it["source_url"] = url_map.get(it.get("source_files") or "", "")
    # 按维度分组，组内按 严重度(P0先) → 保障阶段(先发生在前) 排序
    sev_order = {s: i for i, s in enumerate(SEVERITIES)}
    grouped = {d: [] for d in DIMENSIONS}
    for it in items:
        grouped.setdefault(it["dimension"], []).append(it)
    for d in grouped:
        grouped[d].sort(key=lambda x: (sev_order.get(x.get("severity") or "P2", 9),
                                       STAGE_ORDER.get(x.get("stage") or "未分类", 99),
                                       x.get("sort_order", 0)))
    return {"checklist": dict(cl), "dimensions": DIMENSIONS, "stages": STAGES,
            "severities": SEVERITIES,
            "grouped": grouped, "item_count": len(items),
            "handled_count": sum(1 for it in items if it["handled"])}


class ItemUpdate(BaseModel):
    dimension: str | None = None
    severity: str | None = None
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
    await _require_login(request)
    fields, vals = [], []
    for k in ("dimension", "severity", "stage", "team", "owner", "phenomenon", "cause", "handling", "suggestion", "timing"):
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
    severity: str = "P2"
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
    await _require_login(request)
    if req.dimension not in DIMENSIONS:
        raise HTTPException(400, "dimension 必须是六维度之一")
    stage = req.stage if req.stage in STAGES else "未分类"
    sev = req.severity if req.severity in SEVERITIES else "P2"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT MAX(sort_order) FROM checklist_items WHERE checklist_id = ?",
                               (checklist_id,))
        mx = (await cur.fetchone())[0] or 0
        cur = await db.execute(
            """INSERT INTO checklist_items
               (checklist_id, dimension, severity, stage, team, owner, phenomenon, cause, handling, suggestion, timing, source_files, sort_order)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (checklist_id, req.dimension, sev, stage, req.team, req.owner, req.phenomenon,
             req.cause, req.handling, req.suggestion, req.timing, "(手动添加)", mx + 1))
        await db.commit()
        return {"id": cur.lastrowid, "message": "已添加"}


@router.delete("/checklist/item/{item_id}")
async def delete_item(item_id: int, request: Request):
    await _require_login(request)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM checklist_items WHERE id = ?", (item_id,))
        await db.commit()
    return {"message": "已删除"}


@router.delete("/checklist/{checklist_id}")
async def delete_checklist(checklist_id: int, request: Request):
    await _require_login(request)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM checklist_items WHERE checklist_id = ?", (checklist_id,))
        await db.execute("DELETE FROM checklists WHERE id = ?", (checklist_id,))
        await db.commit()
    _GEN_PROGRESS.pop(checklist_id, None)
    return {"message": "已删除"}
