"""直播日历：按时间范围查询重要直播场次(过去PCU / 未来预约)。
读接口只读库；报备场次刷新接口(refresh-reports)会重新从内部审批中台
拉取报备单最新状态、幂等回写 is_report 行(不触碰非报备场次)。"""
import os
import json
import time
import asyncio
import importlib.util
from datetime import datetime, timedelta, timezone
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from database import DB_PATH
from auth import require_login

router = APIRouter(tags=["live-calendar"])

# 房间地址URL前缀(生产经环境变量注入，公开仓库默认占位符)
ROOM_URL_PREFIX = os.environ.get("LIVE_ROOM_URL_PREFIX", "https://live.example.com/")

BJ_TZ = timezone(timedelta(hours=8))

# 报备取数脚本路径(在 .gitignore 内、仅生产部署;公开仓库无此文件,接口优雅降级为 503)
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INGEST_REPORTS_PATH = os.path.join(_BACKEND_DIR, "temp_tasks", "eeflow", "ingest_reports.py")

# 手动刷新报备的【直播日期】向后窗口(天):刷新"今天 + 之后 N 天"的报备场次。
# 报备本质是未来的直播计划审批,取未来更合理(默认 10,即今天+10天=共11个自然日)。
_REFRESH_FORWARD_DAYS = int(os.environ.get("LIVECAL_REFRESH_FORWARD_DAYS", "10"))

# 审批查询回看窗(天):与直播日期窗解耦。ingest_reports.run 的 start/end 过滤的是
# 【审批处理/发起时间】而非直播日期——未来某天的直播,其报备单通常是过去审批/发起的。
# 故审批窗口须向【过去】回看足够宽,才能捞全"直播落在未来10天、但审批发生在过去"的单;
# 真正决定入库哪些天的是 day_filter=[today, today+FORWARD]。90天回看覆盖提前报备的常见提前期。
_APPROVAL_LOOKBACK_DAYS = int(os.environ.get("LIVECAL_REFRESH_APPROVAL_LOOKBACK_DAYS", "90"))

# 并发护栏:报备刷新会打外部审批中台,串行化防重复点击/并发打爆外部系统。
_refresh_lock = asyncio.Lock()


def _room_url(room_id: str) -> str:
    return f"{ROOM_URL_PREFIX}{room_id}" if room_id else ""


def _load_ingest_reports():
    """动态加载报备取数模块(gitignore 内、仅生产存在)。缺失则抛错供接口降级。"""
    if not os.path.exists(_INGEST_REPORTS_PATH):
        raise FileNotFoundError("报备取数模块未部署(仅生产环境可用)")
    spec = importlib.util.spec_from_file_location("ingest_reports_api", _INGEST_REPORTS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("报备取数模块加载失败")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _snapshot_report_window(con, day_lo: str, day_hi: str) -> dict:
    """快照目标直播日期窗口内的报备场次,返回 {(order_id, session_time): fingerprint}。
    fingerprint 取影响展示的关键字段(标题/房间/时段/预估/审批状态/卡点),用于刷新前后
    对比出真正的【新增/变更/删除】,而非"回写了几条"(每次全删全插,回写数≈窗口总数,无法感知变化)。"""
    rows = con.execute(
        "SELECT report_order_id, session_time, title, room_id, report_info "
        "FROM live_sessions WHERE is_report=1 AND session_time >= ? AND session_time <= ?",
        (f"{day_lo} 00:00:00", f"{day_hi} 23:59:59")).fetchall()
    snap = {}
    for oid, st, title, room_id, ri in rows:
        info = {}
        if ri:
            try:
                info = json.loads(ri)
            except Exception:
                info = {}
        # 指纹:仅纳入展示相关字段,避免无关字段抖动误判为"变更"
        fp = (
            title or "", room_id or "",
            info.get("time_start", ""), info.get("time_end", ""),
            str(info.get("pcu", "")), info.get("pcu_display", ""),
            info.get("order_type", ""),
            info.get("pending_actor", ""), info.get("pending_node", ""),
            info.get("creator", ""), info.get("creator_first_dept", ""),
        )
        snap[(oid, st)] = fp
    return snap


def _diff_report_snapshots(before: dict, after: dict) -> dict:
    """对比刷新前后快照,返回 {added, changed, removed} 计数。
    key=(order_id, session_time):before 无 after 有=新增;两边都有但指纹不同=变更;
    before 有 after 无=删除(如报备被撤回/驳回、或场次移出窗口)。"""
    before_keys = set(before)
    after_keys = set(after)
    added = len(after_keys - before_keys)
    removed = len(before_keys - after_keys)
    changed = sum(1 for k in (before_keys & after_keys) if before[k] != after[k])
    return {"added": added, "changed": changed, "removed": removed}


def _run_report_refresh() -> dict:
    """同步执行报备刷新(阻塞:走外部审批中台 + sqlite),由上层放到线程池里跑。
    刷新【今天 ~ 今天+FORWARD 天】的报备场次(按直播日期):审批查询宽窗回看以捞全
    提前报备的未来单,再按直播日期收敛入库。只增删该窗口内 is_report=1 的报备场次,
    PCU/预约场次及窗口外的历史报备行均不受影响。返回契约字段 dict。
    ingest_reports 内部用相对路径('temp_tasks/eeflow/ingest_reports.py')及 DB_PATH,
    需以 backend/ 为工作目录运行(与每日任务一致)。"""
    t0 = time.monotonic()
    prev_cwd = os.getcwd()
    os.chdir(_BACKEND_DIR)
    try:
        ir = _load_ingest_reports()
        today = datetime.now(BJ_TZ).date()
        # 目标【直播日期】窗口:今天 ~ 今天+FORWARD(闭区间,共 FORWARD+1 个自然日)
        day_lo = today.isoformat()
        day_hi = (today + timedelta(days=_REFRESH_FORWARD_DAYS)).isoformat()
        day_filter = (day_lo, day_hi)
        # 审批查询窗口:向过去宽窗回看,捞全"直播在未来10天、审批发生在过去"的报备单。
        approval_start = (today - timedelta(days=_APPROVAL_LOOKBACK_DAYS)).isoformat()
        approval_end = day_hi  # 审批时间也可能落在直播日之前的近未来,右界取到窗口末
        # 刷新前快照(用于对比出真正的新增/变更/删除,而非"回写了几条")
        import sqlite3
        snap_con = sqlite3.connect(DB_PATH)
        try:
            before_snap = _snapshot_report_window(snap_con, day_lo, day_hi)
        finally:
            snap_con.close()
        stats = {"failed": 0, "errors": [], "by_type": {}}
        n_orders, n_records = ir.run(
            approval_start, approval_end, write=True, stats=stats, day_filter=day_filter)
        # 刷新后快照 + 对比
        snap_con = sqlite3.connect(DB_PATH)
        try:
            after_snap = _snapshot_report_window(snap_con, day_lo, day_hi)
        finally:
            snap_con.close()
        changes = _diff_report_snapshots(before_snap, after_snap)
        changes["total_changed"] = changes["added"] + changes["changed"] + changes["removed"]
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return {
            "ok": True,
            "total": n_orders,                    # 命中报备单据数(去重后的 order 数,含窗口外天的单)
            "updated": n_records,                 # 回写日历的报备场次条数(仅目标直播日期窗口内的天)
            "failed": stats.get("failed", 0),     # 详情拉取失败的单数
            "changes": changes,                   # 本次真实变化:{added,changed,removed,total_changed}
            "elapsed_ms": elapsed_ms,
            "errors": (stats.get("errors") or [])[:50],   # [{order_id, name, reason}]
            "by_type": stats.get("by_type", {}),  # {已完结:n, 审批中:n, ...}
            "window": {
                "start": day_lo, "end": day_hi,   # 目标直播日期窗口(如实反映本次刷新范围)
                "forward_days": _REFRESH_FORWARD_DAYS,
                "approval_start": approval_start, "approval_end": approval_end,
            },
        }
    finally:
        os.chdir(prev_cwd)


@router.post("/live-calendar/refresh-reports")
async def refresh_reports(user: dict = Depends(require_login)):
    """手动刷新"报备场次"(同步,无入参)。刷新【今天 ~ 今天+10天】(可调)的报备单最新状态,
    幂等回写该直播日期窗口内 live_sessions 的 is_report=1 行,不触碰 PCU/预约等非报备场次、
    也不动窗口外的历史报备行。并发时返回 409;取数模块未部署返回 503;外部拉取异常返回 502。
    访客(未登录)不允许触发,返回 401。
    返回契约:{ ok, total, updated, failed, elapsed_ms, errors[], by_type{}, window{start,end,forward_days,...} }。"""
    if user.get("is_guest"):
        raise HTTPException(status_code=401, detail="请先登录后再刷新报备场次")
    if _refresh_lock.locked():
        raise HTTPException(status_code=409, detail="报备刷新正在进行中，请稍候再试")
    async with _refresh_lock:
        try:
            return await asyncio.to_thread(_run_report_refresh)
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"报备刷新失败: {str(e)[:200]}")


@router.get("/live-calendar/sessions")
async def list_sessions(start: str, end: str, user: dict = Depends(require_login)):
    """按时间范围[start, end]返回直播场次，按时间升序。start/end 为 'YYYY-MM-DD' 或带时分秒。"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT id, session_time, title, anchor_name, pcu, reservation, room_id, "
            "watch_hours_fans, watch_hours_all, danmu_fans, danmu_all, "
            "enter_dau_fans, enter_dau_all, fans_growth_fans, fans_growth_all, "
            "is_report, report_info, is_dirty, dirty_note "
            "FROM live_sessions WHERE session_time >= ? AND session_time <= ? "
            "ORDER BY session_time ASC",
            (start, end + " 23:59:59" if len(end) == 10 else end),
        )).fetchall()
    return [
        {
            "id": r["id"],
            "session_time": r["session_time"],
            "title": r["title"],
            "anchor_name": r["anchor_name"] or "",
            "pcu": r["pcu"],
            "reservation": r["reservation"],
            "room_id": r["room_id"] or "",
            "room_url": _room_url(r["room_id"] or ""),
            "watch_hours_fans": r["watch_hours_fans"],
            "watch_hours_all": r["watch_hours_all"],
            "danmu_fans": r["danmu_fans"],
            "danmu_all": r["danmu_all"],
            "enter_dau_fans": r["enter_dau_fans"],
            "enter_dau_all": r["enter_dau_all"],
            "fans_growth_fans": r["fans_growth_fans"],
            "fans_growth_all": r["fans_growth_all"],
            "is_report": r["is_report"],
            "report_info": json.loads(r["report_info"]) if r["report_info"] else None,
            "is_dirty": r["is_dirty"] or 0,
            "dirty_note": r["dirty_note"] or "",
        }
        for r in rows
    ]
