"""直播日历：按时间范围查询重要直播场次(过去PCU / 未来预约)。
数据写入由外部取数流程负责，本路由只读。"""
import os
import aiosqlite
from fastapi import APIRouter, Depends
from database import DB_PATH
from auth import require_login

router = APIRouter(tags=["live-calendar"])

# 房间地址URL前缀(生产经环境变量注入，公开仓库默认占位符)
ROOM_URL_PREFIX = os.environ.get("LIVE_ROOM_URL_PREFIX", "https://live.example.com/")


def _room_url(room_id: str) -> str:
    return f"{ROOM_URL_PREFIX}{room_id}" if room_id else ""


@router.get("/live-calendar/sessions")
async def list_sessions(start: str, end: str, user: dict = Depends(require_login)):
    """按时间范围[start, end]返回直播场次，按时间升序。start/end 为 'YYYY-MM-DD' 或带时分秒。"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT id, session_time, title, anchor_name, pcu, reservation, room_id, "
            "watch_hours, danmu_count, fans_growth "
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
            "watch_hours": r["watch_hours"],
            "danmu_count": r["danmu_count"],
            "fans_growth": r["fans_growth"],
        }
        for r in rows
    ]
