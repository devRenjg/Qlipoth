import uuid
import secrets
import hashlib
import re
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel
import aiosqlite
from database import DB_PATH

router = APIRouter(tags=["user"])

_BJ_TZ = timezone(timedelta(hours=8))
COOKIE_NAME = "qlipoth_token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year(登录后长期保持,不重复弹窗)
# 生产 HTTPS 部署设 QLIPOTH_COOKIE_SECURE=1 启用 Secure；本地开发默认关(否则 http 无法调试登录)
import os as _os
COOKIE_SECURE = _os.environ.get("QLIPOTH_COOKIE_SECURE", "").lower() in ("1", "true", "yes")

ROLES = {"admin": 0, "super": 1, "user": 2}


def _now_bj() -> str:
    return datetime.now(_BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


async def _user_from_token(request: Request) -> dict | None:
    """从 cookie token 解析当前用户，返回 {id,username,role} 或 None。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, username, role FROM users WHERE token = ?", (token,))).fetchone()
    return {"id": row["id"], "username": row["username"], "role": row["role"]} if row else None


# ── 内网 SSO 访客身份 ──────────────────────────────────────────────
# 公司内网员工浏览器带有 .internal.example 顶级域下的明文 cookie `username`(如 employee_id)。
# nginx 反代默认透传 Cookie,后端直接读 request.cookies['username'] 拿到内网昵称。
#
# 🔴 安全边界(明文 cookie 可伪造,务必守住):
#   - username 仅用于「显示昵称 + 问答归属」,绝不作为任何权限依据。
#   - SSO 用户 role 恒为 'user',并带 is_guest=True → 所有写/删/导入/管理操作
#     仍由各接口的 require_admin / _require_login(查 qlipoth_token) / is_guest 拦截。
#   - SSO 用户走独立命名空间 bili_uid='sso:<name>',只按此列匹配,绝不按 username
#     关联正式注册账号(否则伪造 username=admin 的 cookie 会命中正式 admin 账号提权)。
SSO_COOKIE_NAME = "username"
_SSO_UID_PREFIX = "sso:"
# 明文昵称的合法字符,防止 cookie 注入奇怪值(内网用户名为英文/数字/下划线/点/连字符)
_SSO_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,32}$")


def _sanitize_sso_name(raw: str | None) -> str | None:
    """校验并规整 SSO cookie 里的明文用户名,非法返回 None。"""
    if not raw:
        return None
    name = raw.strip()
    if not _SSO_NAME_RE.match(name):
        return None
    return name


async def _sso_guest_from_cookie(request: Request) -> dict | None:
    """从 .internal.example 的明文 username cookie 解析内网访客身份。
    返回 {id, username, role:'user', is_guest:True, is_sso:True} 或 None。
    会把该内网用户 upsert 到 users 表(独立命名空间),以便问答归属能落到具体人。"""
    name = _sanitize_sso_name(request.cookies.get(SSO_COOKIE_NAME))
    if not name:
        return None
    uid = _SSO_UID_PREFIX + name
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, username FROM users WHERE bili_uid = ?", (uid,))).fetchone()
        if row:
            await db.execute(
                "UPDATE users SET last_seen = ? WHERE id = ?", (_now_bj(), row["id"]))
            await db.commit()
            uid_id, uname = row["id"], row["username"]
        else:
            # 免密 SSO 用户:password_hash/salt NOT NULL,填占位值;token 留空(无正式登录态)。
            # role 恒为 'user'。username 可能与正式账号同名,但 bili_uid 命名空间隔离,互不影响。
            try:
                cur = await db.execute(
                    "INSERT INTO users (bili_uid, username, password_hash, password_salt, role, token, last_seen) "
                    "VALUES (?, ?, '', '', 'user', NULL, ?)",
                    (uid, name, _now_bj()),
                )
                await db.commit()
                uid_id, uname = cur.lastrowid, name
            except Exception:
                # username UNIQUE 冲突(正式账号已占该名):退回按 bili_uid 再查一次;
                # 仍无则用带后缀的展示名落库,保证 SSO 用户始终有独立行、绝不复用正式账号。
                row2 = await (await db.execute(
                    "SELECT id, username FROM users WHERE bili_uid = ?", (uid,))).fetchone()
                if row2:
                    uid_id, uname = row2["id"], row2["username"]
                else:
                    alt = f"{name}#sso"
                    cur = await db.execute(
                        "INSERT INTO users (bili_uid, username, password_hash, password_salt, role, token, last_seen) "
                        "VALUES (?, ?, '', '', 'user', NULL, ?)",
                        (uid, alt, _now_bj()),
                    )
                    await db.commit()
                    uid_id, uname = cur.lastrowid, alt
    return {"id": uid_id, "username": uname, "role": "user", "is_guest": True, "is_sso": True}


# 访客身份:未登录用户默认以访客(普通用户权限)访问,不强制登录。
# 只读及普通用户操作放行;写操作/管理操作仍由各接口的本人校验或 require_admin 拦截。
GUEST_USER = {"id": 0, "username": "访客", "role": "user", "is_guest": True}


async def require_login(request: Request) -> dict:
    """统一登录依赖。身份优先级:正式 token 登录 > 内网 SSO 明文 cookie > 匿名访客。
    需要管理员/写权限的接口用 require_admin 或自行校验 user['id']/is_guest。"""
    u = await _user_from_token(request)
    if u:
        return u
    sso = await _sso_guest_from_cookie(request)
    if sso:
        return sso
    return dict(GUEST_USER)


async def require_admin(request: Request) -> dict:
    """统一管理员依赖：非 admin 抛 403。"""
    u = await _user_from_token(request)
    if not u:
        raise HTTPException(401, "未登录")
    if u["role"] != "admin":
        raise HTTPException(403, "需要管理员权限")
    return u


def _hash_password(password: str, salt: str = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return hashed, salt


def _verify_password(password: str, hashed: str, salt: str) -> bool:
    check, _ = _hash_password(password, salt)
    return check == hashed


def _validate_password(password: str) -> str | None:
    """Returns error message if invalid, None if OK."""
    if len(password) < 8:
        return "密码至少 8 位"
    if not re.search(r'[A-Z]', password):
        return "密码需包含大写字母"
    if not re.search(r'[a-z]', password):
        return "密码需包含小写字母"
    if not re.search(r'[0-9]', password):
        return "密码需包含数字"
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{}|;:,.<>?/~]', password):
        return "密码需包含特殊字符"
    return None


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


@router.get("/user/me")
async def get_current_user(request: Request):
    """Get current user. 优先级:正式 token 登录 > 内网 SSO 明文 cookie > 未登录(None)。
    SSO 内网访客返回真实昵称 + is_guest/is_sso 标记,前端据此右上显示昵称、并保留登录入口。"""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT id, username, role FROM users WHERE token = ?", (token,))
            row = await cursor.fetchone()
            if row:
                await db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (_now_bj(), row["id"]))
                await db.commit()
                return {"user": {"id": row["id"], "username": row["username"], "role": row["role"]}}

    sso = await _sso_guest_from_cookie(request)
    if sso:
        return {"user": {"id": sso["id"], "username": sso["username"], "role": "user",
                         "is_guest": True, "is_sso": True}}
    return {"user": None}


@router.post("/user/register")
async def register_user(req: RegisterRequest, response: Response):
    """Register a new user."""
    username = req.username.strip()
    if not username or len(username) > 20 or len(username) < 2:
        raise HTTPException(400, "用户名长度 2-20 个字符")

    pwd_err = _validate_password(req.password)
    if pwd_err:
        raise HTTPException(400, pwd_err)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM users WHERE username = ?", (username,))
        if await cursor.fetchone():
            raise HTTPException(409, f"用户名「{username}」已被注册")

        hashed, salt = _hash_password(req.password)
        token = secrets.token_urlsafe(32)

        await db.execute(
            "INSERT INTO users (bili_uid, username, password_hash, password_salt, role, token, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4())[:8], username, hashed, salt, "user", token, _now_bj()),
        )
        await db.commit()

        cursor = await db.execute("SELECT id, role FROM users WHERE token = ?", (token,))
        row = await cursor.fetchone()

    response.set_cookie(COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return {"user": {"id": row[0], "username": username, "role": row[1]}}


@router.post("/user/login")
async def login_user(req: LoginRequest, response: Response):
    """Login with username and password."""
    username = req.username.strip()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(401, "用户名或密码错误")

        if not _verify_password(req.password, row["password_hash"], row["password_salt"]):
            raise HTTPException(401, "用户名或密码错误")

        token = secrets.token_urlsafe(32)
        await db.execute("UPDATE users SET token = ?, last_seen = ? WHERE id = ?", (token, _now_bj(), row["id"]))
        await db.commit()

    response.set_cookie(COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return {"user": {"id": row["id"], "username": row["username"], "role": row["role"]}}


@router.post("/user/logout")
async def logout_user(request: Request, response: Response):
    """Clear user cookie and invalidate token."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET token = NULL WHERE token = ?", (token,))
            await db.commit()
    response.delete_cookie(COOKIE_NAME)
    return {"message": "已退出"}


@router.get("/user/list")
async def list_users(request: Request):
    """List all users. Admin only."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT role FROM users WHERE token = ?", (token,))
        caller = await cursor.fetchone()
        if not caller or caller["role"] != "admin":
            raise HTTPException(403, "无权限")
        # 按最后活跃时间倒序，最新登录/活跃的用户排在最前；从未活跃(last_seen NULL)沉底
        cursor = await db.execute(
            "SELECT id, username, role, last_seen FROM users "
            "ORDER BY last_seen IS NULL, last_seen DESC, id DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


@router.get("/user/{user_id}/activity")
async def user_activity(user_id: int, request: Request, limit: int = 100):
    """查询某用户的行为日志（问答/生成清单/导出等）。Admin only。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        caller = await (await db.execute("SELECT role FROM users WHERE token = ?", (token,))).fetchone()
        if not caller or caller["role"] != "admin":
            raise HTTPException(403, "无权限")
        rows = await (await db.execute(
            "SELECT action, detail, created_at FROM user_activity WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, min(limit, 500)))).fetchall()
        # 各类型计数汇总
        stats = await (await db.execute(
            "SELECT action, COUNT(*) c FROM user_activity WHERE user_id = ? GROUP BY action ORDER BY c DESC",
            (user_id,))).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "stats": [dict(r) for r in stats],
        }


class RoleUpdateRequest(BaseModel):
    role: str


@router.put("/user/{user_id}/role")
async def update_user_role(user_id: int, req: RoleUpdateRequest, request: Request):
    """Update user role. Admin only."""
    if req.role not in ("admin", "super", "user"):
        raise HTTPException(400, "无效角色")
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT role FROM users WHERE token = ?", (token,))
        caller = await cursor.fetchone()
        if not caller or caller["role"] != "admin":
            raise HTTPException(403, "无权限")
        await db.execute("UPDATE users SET role = ? WHERE id = ?", (req.role, user_id))
        await db.commit()
    return {"message": "已更新"}


@router.delete("/user/{user_id}")
async def delete_user(user_id: int, request: Request):
    """Delete a user. Admin only."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, role FROM users WHERE token = ?", (token,))
        caller = await cursor.fetchone()
        if not caller or caller["role"] != "admin":
            raise HTTPException(403, "无权限")
        if caller["id"] == user_id:
            raise HTTPException(400, "不能删除自己")
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()
    return {"message": "已删除"}
