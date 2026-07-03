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
from dimensions import DIMENSION_LABELS as DIMENSIONS
# 严重度分级
SEVERITIES = ["P0", "P1", "P2"]
# 保障周期阶段（按发生先后排序，先发生在前）
STAGES = ["备战前期", "压测演练", "上线前", "活动当天", "活动后", "未分类"]
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}
REVIEW_KEYWORDS = ("复盘", "总结", "问题", "故障")
CONTENT_LIMIT = 8000
CROSS_PER_DIM = 10  # 跨活动借鉴每维度最多保留条数，防止灌水淹没本活动

# 抽取用模型与并发：默认空=用 settings.llm_model（opus，质量最高但慢）。
# 批量重建场景可在运行前覆盖为更快的模型并提高并发，把数百篇文档压到可接受时间。
EXTRACT_MODEL = ""          # 空 → settings.llm_model
EXTRACT_CONCURRENCY = 4     # _generate_task 抽取并发度

# 生成任务的内存进度（checklist 生成是低频长任务，前端轮询）
_GEN_PROGRESS: dict = {}


def _now():
    return datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")


EXTRACT_SYS = f"""你在为"大型直播活动保障"团队，从一篇历史活动文档中提炼"踩过的坑/教训/事故"，
帮助下一届活动备战时主动规避。请把每一条归到下面六个维度之一：
- 事故/故障：发生过的重大事故、资损、严重故障、影响用户的线上问题。**判定优先级最高：只要造成了重大影响或资损，无论起因是攻击、bug、容量、还是依赖故障，都必须归到本维度，不要因为"起因是安全攻击"就归到安全维度。**
- 高可用保障：能看能播、PCU、降级、限流、容灾、架构、压测；流程/工具/机制类技术沉淀也归这里
- 直播播放体验：卡顿、延迟、清晰度、首帧、画质
- 安全：弹幕安全(审核/真实性/黑灰产/冲塔)、业务安全(风控/作弊/资损防护)、基础技术安全(攻击/防护)。**仅当是安全防护措施/风险点、尚未酿成重大事故时归这里；一旦造成了重大故障或资损，归"事故/故障"。**
- 业务需求：增长目标相关的功能/玩法需求层面的问题；运营/排期/项目协调类也归这里
- 成本：带宽、采购、服务器预算（注意采购是否需要更早前置介入）

只提炼文档中**确实发生过的问题/故障/事故/教训/改进项**，不要编造、不要把"做得好的地方"或
"纯方案设想"当坑。若文档是纯方案/需求/名单类、没有实际踩坑内容，返回空数组。

**数据务必带上**：文档里出现的具体数据(PCU/在线人数峰值、QPS、带宽峰值、影响时长、资损金额、
错误率、延迟、压测量级、扩容倍数、时间点等)，**直接写进 phenomenon 或 suggestion 文字里**，
不要丢。例如"决赛 PCU 达 620w 超预期 20%，触发限流"而非笼统的"在线人数超预期"。

为每条标注：
- severity 严重度：P0(重大事故/资损/严重故障/大面积影响用户) / P1(重要，需重点关注) / P2(一般改进项)。**凡造成重大影响或资损的，一律 P0，不论起因。**
- stage 保障周期阶段：备战前期 / 压测演练 / 上线前 / 活动当天 / 活动后；判不出填"未分类"
- team 负责团队/业务线（流媒体、基架、弹幕、OTT/TV端、产品、研发、质量保障、直播体验、带宽/成本、安全 等），尽量填
- owner 负责人：仅当出现**真实中文姓名**时填，脱敏ID(@eJP3类)、字体名、纯数字一律留空
- cross_activity 是否跨活动通用：对其他大型活动(春晚/跨晚/S赛)也有借鉴价值则 true，纯本活动特有 false。**注意：凡是"事故/故障"维度的条目，一律填 true（重大事故是所有活动都必须警示借鉴的）。**

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


def _dedup_key(it: dict) -> str:
    """同一事件的归一化指纹：去标点/空白/数字后取现象前缀，让"同一坑不同措辞/不同数据"归并。"""
    import re as _re
    text = (it.get("phenomenon") or "") + (it.get("cause") or "")
    norm = _re.sub(r"[\s\d，。、；：,.;:!?？！（）()\[\]【】\"'`~\-—_/\\]+", "", text)
    return norm[:28]


def _dedup_items(items: list[dict]) -> list[dict]:
    """全清单级查重（文本指纹兜底版）：同指纹只保留最值得留的一条。

    指纹相对保守（同事件不同措辞可能漏并），主去重交给 _llm_dedup_items；本函数
    作为 LLM 失败时的兜底，至少把"逐字接近"的重复合并掉。
    """
    sev_rank = {"P0": 0, "P1": 1, "P2": 2}

    def completeness(it: dict) -> int:
        return sum(len(it.get(f) or "") for f in ("phenomenon", "cause", "handling", "suggestion"))

    def better(a: dict, b: dict) -> dict:
        a_own = 0 if not a.get("cross_from") else 1
        b_own = 0 if not b.get("cross_from") else 1
        ka = (a_own, sev_rank.get(a.get("severity", "P2"), 9), -completeness(a))
        kb = (b_own, sev_rank.get(b.get("severity", "P2"), 9), -completeness(b))
        return a if ka <= kb else b

    best: dict[str, dict] = {}
    for it in items:
        key = _dedup_key(it)
        if not key:
            key = f"__empty__{id(it)}"
        if key not in best:
            best[key] = it
            continue
        keep = better(best[key], it)
        drop = it if keep is best[key] else best[key]
        if not keep.get("owner") and drop.get("owner"):
            keep["owner"] = drop["owner"]
        if not keep.get("team") and drop.get("team"):
            keep["team"] = drop["team"]
        best[key] = keep
    return list(best.values())


DEDUP_SYS = """你在给"大型直播活动保障"的踩坑清单做查重。下面是一批已编号的踩坑条目（每条只给现象+原因摘要）。
请找出其中**指向同一个事件/同一个坑**的条目，把它们归为一组（哪怕措辞、数据、详略不同，只要是同一件事就算重复）。
判定要谨慎：只有确实是同一事件才归并；相似主题但不同具体事件（如"第一轮压测告警"和"第二轮压测告警"是两件事）不要合并。

返回严格 JSON（不要 markdown）：
{"groups": [[1, 5, 9], [3, 12]]}
groups 里每个子数组是一组重复条目的编号（至少2个才列出）；没有任何重复就返回 {"groups": []}。只输出 JSON。"""


async def _llm_dedup_items(items: list[dict]) -> list[dict]:
    """LLM 语义查重：把指向同一事件的条目合并，一个事件只保留一条。

    保留规则同 _dedup_items（本活动>跨活动、P0>P1>P2、字段更全），并把被淘汰条目
    的非空 owner/team 补到留下的那条。LLM 失败则回退到 _dedup_items 文本兜底。
    """
    if len(items) < 2:
        return items
    sev_rank = {"P0": 0, "P1": 1, "P2": 2}

    def completeness(it: dict) -> int:
        return sum(len(it.get(f) or "") for f in ("phenomenon", "cause", "handling", "suggestion"))

    def pick(group: list[int]) -> int:
        # 从一组重复里挑最该保留的下标
        def k(idx):
            it = items[idx]
            own = 0 if not it.get("cross_from") else 1
            return (own, sev_rank.get(it.get("severity", "P2"), 9), -completeness(it))
        return min(group, key=k)

    lines = []
    for i, it in enumerate(items):
        ph = (it.get("phenomenon") or "").replace("\n", " ")[:60]
        ca = (it.get("cause") or "").replace("\n", " ")[:30]
        lines.append(f"{i}. 现象:{ph} | 原因:{ca}")
    user = "\n".join(lines)

    try:
        txt, _ = await llm.chat_completion(
            [{"role": "system", "content": DEDUP_SYS}, {"role": "user", "content": user}],
            temperature=0, model=load_settings().llm_model)
        txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        groups = json.loads(txt).get("groups", [])
    except Exception:  # noqa: BLE001
        return _dedup_items(items)

    drop_idx: set[int] = set()
    for group in groups:
        group = [g for g in group if isinstance(g, int) and 0 <= g < len(items)]
        if len(group) < 2:
            continue
        keep = pick(group)
        for idx in group:
            if idx == keep:
                continue
            # 归属补全
            if not items[keep].get("owner") and items[idx].get("owner"):
                items[keep]["owner"] = items[idx]["owner"]
            if not items[keep].get("team") and items[idx].get("team"):
                items[keep]["team"] = items[idx]["team"]
            drop_idx.add(idx)

    return [it for i, it in enumerate(items) if i not in drop_idx]


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


async def _require_login(request: Request) -> dict:
    """写操作用:必须真登录(访客/未登录抛401)。返回 {id, username, role}。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, username, role FROM users WHERE token = ?", (token,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(401, "登录已失效")
        return {"id": row["id"], "username": row["username"], "role": row["role"]}


async def _optional_login(request: Request) -> dict:
    """只读查看用:登录返回真实用户,未登录返回访客(普通用户权限)。
    保障清单查看(列表/详情/进度)对所有人开放,访客也能看别人生成的清单。"""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT id, username, role FROM users WHERE token = ?", (token,))
            row = await cur.fetchone()
            if row:
                return {"id": row["id"], "username": row["username"], "role": row["role"]}
    return {"id": 0, "username": "访客", "role": "user", "is_guest": True}


async def _require_checklist_owner(checklist_id: int, request: Request) -> dict:
    """写操作鉴权：仅清单生成者本人可写（admin 也不例外）。返回当前用户。

    用于删除/编辑/勾选/导出等所有写操作；非生成者只能读，写一律 403。
    历史清单（created_by 为空、无归属）放开给所有登录用户，避免老数据没人能维护。
    """
    me = await _require_login(request)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT created_by FROM checklists WHERE id = ?", (checklist_id,))).fetchone()
    if not row:
        raise HTTPException(404, "清单不存在")
    owner = row["created_by"] or ""
    if owner and owner != me["username"]:
        raise HTTPException(403, f"该清单由「{owner}」生成，你只能查看，不能修改")
    return me


async def _checklist_id_of_item(item_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT checklist_id FROM checklist_items WHERE id = ?", (item_id,))).fetchone()
    if not row:
        raise HTTPException(404, "条目不存在")
    return row["checklist_id"]


async def _extract_from_doc(name: str, content: str, sem: asyncio.Semaphore) -> list[dict]:
    """对一篇文档抽取踩坑条目。失败返回空（best-effort）。"""
    async with sem:
        user = f"文档标题：{name}\n\n文档内容：\n{content[:CONTENT_LIMIT]}"
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": EXTRACT_SYS}, {"role": "user", "content": user}],
                temperature=0, model=EXTRACT_MODEL or load_settings().llm_model)
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
        sem = asyncio.Semaphore(EXTRACT_CONCURRENCY)

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

        # 跨活动借鉴：事故/故障维度必借(重大事故所有活动都该警示)，其余按 cross_activity + 去重 + 每维限额
        cross_raw = []
        for oa, rows in cross_docs.items():
            items = await _scan_docs(rows, kb, sem, prog, prog["total"])
            for it in items:
                if it.get("dimension") == "事故/故障" or it.get("cross_activity"):
                    it["cross_from"] = oa
                    cross_raw.append(it)

        seen = {(it["phenomenon"] or "")[:20] for it in own_items}
        per_dim: dict = {}
        cross_items = []
        for it in cross_raw:
            key = (it["phenomenon"] or "")[:20]
            if key in seen:
                continue
            is_incident = it.get("dimension") == "事故/故障"
            n = per_dim.get(it["dimension"], 0)
            # 事故/故障不受每维限额约束(必须全部借鉴警示)
            if not is_incident and n >= CROSS_PER_DIM:
                continue
            seen.add(key)
            per_dim[it["dimension"]] = n + 1
            cross_items.append(it)

        all_items = own_items + cross_items

        # 全清单级查重：一个事件只出现一次（LLM 语义判同，跨维度/跨文档/跨活动统一去重）
        before = len(all_items)
        all_items = await _llm_dedup_items(all_items)
        prog["deduped"] = before - len(all_items)

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
    me = await _require_login(request)
    if req.activity not in ACTIVITIES:
        raise HTTPException(400, f"activity 必须是 {ACTIVITIES} 之一")
    title = req.title or f"{req.activity} 备战踩坑清单"
    async with aiosqlite.connect(DB_PATH) as db:
        # 同一用户每个活动只能有一份清单，需先删除自己的旧清单才能重新生成
        dup = await (await db.execute(
            "SELECT id FROM checklists WHERE activity = ? AND created_by = ?",
            (req.activity, me["username"]))).fetchone()
        if dup:
            raise HTTPException(409, f"你已生成过「{req.activity}」清单，请先删除自己的旧清单再重新生成")
        cur = await db.execute(
            "INSERT INTO checklists (activity, title, status, created_at, created_by) VALUES (?,?,?,?,?)",
            (req.activity, title, "generating", _now(), me["username"]))
        await db.commit()
        checklist_id = cur.lastrowid
    _GEN_PROGRESS[checklist_id] = {"status": "generating", "total": 0, "processed": 0}
    asyncio.create_task(_generate_task(req.activity, checklist_id))
    try:
        from activity import log_activity, ACT_CHECKLIST_GEN
        await log_activity(me["id"], me["username"], ACT_CHECKLIST_GEN, f"{req.activity}《{title}》")
    except Exception:
        pass
    return {"id": checklist_id, "title": title, "status": "generating", "created_by": me["username"]}


@router.get("/checklist/generate/{checklist_id}/progress")
async def generate_progress(checklist_id: int, request: Request):
    await _optional_login(request)
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
    await _optional_login(request)
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
    await _optional_login(request)
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
    # 按维度分组，组内按 严重度(P0先) → 当天发生(优先) → 保障阶段(先发生在前) 排序
    sev_order = {s: i for i, s in enumerate(SEVERITIES)}
    grouped = {d: [] for d in DIMENSIONS}
    for it in items:
        grouped.setdefault(it["dimension"], []).append(it)
    for d in grouped:
        grouped[d].sort(key=lambda x: (sev_order.get(x.get("severity") or "P2", 9),
                                       0 if x.get("day_late") else 1,
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
    cid = await _checklist_id_of_item(item_id)
    await _require_checklist_owner(cid, request)
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
    await _require_checklist_owner(checklist_id, request)
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
    cid = await _checklist_id_of_item(item_id)
    await _require_checklist_owner(cid, request)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM checklist_items WHERE id = ?", (item_id,))
        await db.commit()
    return {"message": "已删除"}


@router.delete("/checklist/{checklist_id}")
async def delete_checklist(checklist_id: int, request: Request):
    await _require_checklist_owner(checklist_id, request)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM checklist_items WHERE checklist_id = ?", (checklist_id,))
        await db.execute("DELETE FROM checklists WHERE id = ?", (checklist_id,))
        await db.commit()
    _GEN_PROGRESS.pop(checklist_id, None)
    return {"message": "已删除"}


# ---------------------------------------------------------------------------
# 导出为企业微信在线文档（Markdown + 复选框 Checklist）
# ---------------------------------------------------------------------------

class ExportWecomReq(BaseModel):
    item_ids: list[int] = []   # 选中的条目；空=导出全部
    title: str | None = None   # 可选自定义文档标题


def _build_export_markdown(activity: str, title: str, items: list[dict]) -> str:
    """把清单条目按六维度组织成带复选框的 Markdown。"""
    dim_order = {d: i for i, d in enumerate(DIMENSIONS)}
    sev_order = {s: i for i, s in enumerate(SEVERITIES)}
    items = sorted(items, key=lambda x: (
        dim_order.get(x.get("dimension"), 99),
        sev_order.get(x.get("severity") or "P2", 9),
        0 if x.get("day_late") else 1,
        STAGE_ORDER.get(x.get("stage") or "未分类", 99),
        x.get("sort_order", 0),
    ))
    total = len(items)
    p0 = sum(1 for it in items if (it.get("severity") == "P0"))
    daylate = sum(1 for it in items if it.get("day_late"))
    by_dim: dict[str, list[dict]] = {}
    for it in items:
        by_dim.setdefault(it.get("dimension") or "未分类", []).append(it)

    lines = [f"# {title}", ""]
    summary = f"> 共 {total} 条 · P0 {p0} 条 · ⚠当天发生 {daylate} 条 · 来源活动：{activity}"
    lines += [summary, ""]

    for dim in DIMENSIONS:
        group = by_dim.get(dim)
        if not group:
            continue
        head = ("## ⚠ " + dim) if dim == "事故/故障" else ("## " + dim)
        lines += [head, ""]
        for it in group:
            check = "x" if it.get("handled") else " "
            sev = it.get("severity") or "P2"
            badge = f"{sev}"
            if it.get("day_late"):
                badge += "·当天发生"
            owner_team = "/".join([x for x in [it.get("team") or "", it.get("owner") or ""] if x])
            cross = f"（借鉴自{it['cross_from']}）" if it.get("cross_from") else ""
            phen = (it.get("phenomenon") or "").replace("\n", " ").strip()
            tail = f" — 负责：{owner_team}" if owner_team else ""
            lines.append(f"- [{check}] 【{badge}】{phen}{cross}{tail}")
            sugg = (it.get("suggestion") or "").replace("\n", " ").strip()
            if sugg:
                lines.append(f"    - 建议：{sugg}")
        lines.append("")
    return "\n".join(lines)


@router.post("/checklist/{checklist_id}/export-wecom")
async def export_checklist_to_wecom(checklist_id: int, req: ExportWecomReq, request: Request):
    me = await _require_checklist_owner(checklist_id, request)
    import wecom

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cl = await (await db.execute("SELECT * FROM checklists WHERE id = ?", (checklist_id,))).fetchone()
        if not cl:
            raise HTTPException(404, "清单不存在")
        cl = dict(cl)
        if req.item_ids:
            qmarks = ",".join("?" * len(req.item_ids))
            rows = await (await db.execute(
                f"SELECT * FROM checklist_items WHERE checklist_id = ? AND id IN ({qmarks}) ORDER BY sort_order, id",
                [checklist_id, *req.item_ids])).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM checklist_items WHERE checklist_id = ? ORDER BY sort_order, id",
                (checklist_id,))).fetchall()
        items = [dict(r) for r in rows]

    if not items:
        raise HTTPException(400, "没有可导出的条目")

    activity = cl.get("activity") or ""
    base_title = req.title or f"{activity} 备战踩坑清单 · 导出 {_now()[:10]}"

    # Windows 命令行单参数上限 ~32000 字符。edit_doc_content 的内容要 JSON 编码后
    # 作为命令行参数传入（换行/引号会转义膨胀），故按"编码后 payload 长度"切分，
    # 而非 markdown 字数。edit_doc_content 是覆写不能追加，超限只能切成多个文档。
    import json as _json
    MAX_PAYLOAD = 22000  # node 直调无 cmd 限制；阈值留余量防企微单文档内容上限

    def _payload_len(its: list[dict]) -> int:
        md = _build_export_markdown(activity, base_title, its)
        return len(_json.dumps({"docid": "x" * 100, "content": md, "content_type": 1}, ensure_ascii=False))

    batches: list[list[dict]] = []
    cur: list[dict] = []
    for it in items:
        if cur and _payload_len(cur + [it]) > MAX_PAYLOAD:
            batches.append(cur)
            cur = [it]
        else:
            cur.append(it)
    if cur:
        batches.append(cur)

    docs = []
    try:
        for idx, batch in enumerate(batches):
            title = base_title if len(batches) == 1 else f"{base_title}（{idx + 1}/{len(batches)}）"
            md = _build_export_markdown(activity, title, batch)
            result = await wecom.create_doc_with_content(title, md)
            docs.append({"url": result["url"], "docid": result["docid"], "count": len(batch), "title": title})
    except wecom.WecomError as e:
        raise HTTPException(502, f"导出到企业微信失败：{e}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"导出异常：{type(e).__name__}: {e}")

    # 兼容前端：单文档时保持原字段，多文档时返回 docs 列表
    first = docs[0]
    try:
        from activity import log_activity, ACT_CHECKLIST_EXPORT
        await log_activity(me["id"], me["username"], ACT_CHECKLIST_EXPORT,
                           f"《{base_title}》{len(items)}条→{len(docs)}个企微文档")
    except Exception:
        pass
    return {
        "url": first["url"], "docid": first["docid"], "title": first["title"],
        "count": len(items), "doc_count": len(docs), "docs": docs,
    }
