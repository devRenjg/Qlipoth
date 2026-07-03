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


# 访客身份:未登录用户默认以访客(普通用户权限)访问,不强制登录。
# 只读及普通用户操作放行;写操作/管理操作仍由各接口的本人校验或 require_admin 拦截。
GUEST_USER = {"id": 0, "username": "访客", "role": "user", "is_guest": True}


async def require_login(request: Request) -> dict:
    """统一登录依赖:未登录返回访客(普通用户权限),不再强制弹窗。
    需要管理员/写权限的接口用 require_admin 或自行校验 user['id']/is_guest。"""
    u = await _user_from_token(request)
    return u if u else dict(GUEST_USER)


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
    """Get current user from cookie token."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return {"user": None}

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, username, role FROM users WHERE token = ?", (token,))
        row = await cursor.fetchone()
        if not row:
            return {"user": None}
        await db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (_now_bj(), row["id"]))
        await db.commit()
        return {"user": {"id": row["id"], "username": row["username"], "role": row["role"]}}


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
        cursor = await db.execute("SELECT id, username, role, last_seen FROM users ORDER BY id")
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
