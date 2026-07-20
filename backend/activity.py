"""用户行为日志：记录平台主要功能使用（问答、生成清单、导出等）。

设计为"尽力而为"——记录失败绝不影响主请求（吞异常）。供各功能端点埋点调用，
及用户管理界面查询展示。
"""
import aiosqlite
from database import DB_PATH

# 行为类型常量（统一命名，便于前端展示与统计）
ACT_QUERY = "问答"
ACT_CHECKLIST_GEN = "生成清单"
ACT_CHECKLIST_EXPORT = "导出清单"
ACT_DOC_IMPORT = "导入文档"
ACT_DOC_DELETE = "删除文档"
ACT_LOGIN = "登录"
ACT_PAGE_VIEW = "访问页面"      # 前端路由切换上报：detail = 页面名 URL
ACT_VIEW_CONTENT = "查看内容"    # 看详情动作上报：detail = 内容类型 + 标题/URL

# 允许前端 track 接口上报的动作白名单（防止任意写入脏动作）
TRACKABLE_ACTIONS = {ACT_PAGE_VIEW, ACT_VIEW_CONTENT}


async def log_activity(user_id, username: str, action: str, detail: str = ""):
    """记录一条用户行为。尽力而为，失败不抛。"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO user_activity (user_id, username, action, detail) VALUES (?,?,?,?)",
                (user_id, username or "", action, (detail or "")[:500]),
            )
            await db.commit()
    except Exception:
        pass


async def current_user_brief(token: str) -> dict:
    """由 token 取 {id, username}，取不到返回空。供埋点处快速拿用户。"""
    if not token:
        return {}
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT id, username FROM users WHERE token = ?", (token,))).fetchone()
            return {"id": row["id"], "username": row["username"]} if row else {}
    except Exception:
        return {}
