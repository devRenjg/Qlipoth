from pathlib import Path
from urllib.parse import urlparse, unquote
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import Response, FileResponse
import httpx
from pydantic import BaseModel
import aiosqlite
from config import load_settings, save_settings
from database import DB_PATH
from auth import COOKIE_NAME, require_login, require_admin
from searcher import read_file_content

router = APIRouter(tags=["documents"])

# wiki.example.com 图片需要登录态 cookie，浏览器直连会被重定向到登录页。
# 这里做一个服务端代理：带 cookie 取图后回传，前端图片改指向本代理。
# 取过的图缓存到本地磁盘，避免同一篇文档反复打开时重复回源（120 张图反复
# 取会把单 worker 的 uvicorn 占满，表现为「服务卡死」）。
_INFO_IMG_HOSTS = {"wiki.example.com"}
_IMG_CONTENT_TYPES = ("image/",)
_IMG_CACHE_DIR = Path(__file__).resolve().parent.parent / ".img_cache"


def _img_cache_path(target: str) -> Path:
    import hashlib
    h = hashlib.sha1(target.encode("utf-8")).hexdigest()
    return _IMG_CACHE_DIR / h


def _ext_to_media(suffix: str) -> str:
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }.get(suffix.lower(), "application/octet-stream")


@router.get("/documents/img-proxy")
async def proxy_info_image(url: str):
    """Fetch an wiki.example.com image server-side (with cookie) and return it.

    Disk-cached; only whitelisted hosts are proxied (not an open proxy).
    """
    target = unquote(url)
    host = urlparse(target).hostname
    if host not in _INFO_IMG_HOSTS:
        raise HTTPException(400, "仅支持代理 wiki.example.com 图片")

    # 1. 命中本地缓存 → 直接返回，不回源
    cache_path = _img_cache_path(target)
    if cache_path.exists():
        suffix = Path(urlparse(target).path).suffix
        return Response(
            content=cache_path.read_bytes(),
            media_type=_ext_to_media(suffix),
            headers={"Cache-Control": "public, max-age=604800"},
        )

    # 2. 回源取图（cookie 读取已带缓存，开销可忽略）
    try:
        from confluence import _get_cookie
        cookie = _get_cookie()
    except Exception:
        cookie = ""

    try:
        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30) as client:
            resp = await client.get(target, headers={"Cookie": cookie} if cookie else {})
    except Exception as e:
        raise HTTPException(502, f"取图失败: {type(e).__name__}")

    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200 or not ctype.startswith(_IMG_CONTENT_TYPES):
        # 多半是 cookie 失效被重定向到登录页
        raise HTTPException(502, "图片不可用（可能 info 登录态已失效）")

    # 落盘缓存
    try:
        _IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(resp.content)
    except Exception:
        pass

    return Response(
        content=resp.content,
        media_type=ctype,
        headers={"Cache-Control": "public, max-age=604800"},
    )


# 企微文档导入时从 base64 落地的本地图片，按 hash 文件名存于 backend/kb_images/
_KB_IMAGES_DIR = Path(__file__).resolve().parent.parent / "kb_images"
_IMG_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp", ".svg": "image/svg+xml"}


@router.get("/documents/kb-image/{name}")
async def get_kb_image(name: str):
    """服务企微文档落地的本地图片。文件名是内容 hash + 扩展名，防目录穿越。"""
    safe = Path(name).name  # 去掉任何路径成分
    fpath = _KB_IMAGES_DIR / safe
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(404, "图片不存在")
    media = _IMG_MEDIA.get(fpath.suffix.lower(), "application/octet-stream")
    return FileResponse(str(fpath), media_type=media, headers={"Cache-Control": "public, max-age=604800"})



async def _require_admin(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT role FROM users WHERE token = ?", (token,))
        caller = await cursor.fetchone()
        if not caller or caller["role"] != "admin":
            raise HTTPException(403, "无权限")


@router.get("/documents")
async def list_documents(user: dict = Depends(require_login)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM documents ORDER BY uploaded_at DESC")
        rows = await cursor.fetchall()
        docs = [dict(row) for row in rows]
        cursor = await db.execute(
            "SELECT dt.document_id, t.id, t.name FROM document_tags dt "
            "JOIN tags t ON dt.tag_id = t.id"
        )
        tag_rows = await cursor.fetchall()
    tags_by_doc: dict[int, list] = {}
    for tr in tag_rows:
        tags_by_doc.setdefault(tr["document_id"], []).append(
            {"id": tr["id"], "name": tr["name"]}
        )
    for doc in docs:
        doc["tags"] = tags_by_doc.get(doc["id"], [])
    return docs


@router.get("/documents/view/{file_name:path}")
async def view_document_by_file(file_name: str, user: dict = Depends(require_login)):
    """View document content rendered as HTML with markdown styling."""
    return _render_md_html(file_name, read_file_content(file_name))


@router.get("/documents/view-old/{file_name:path}")
async def view_old_document(file_name: str, user: dict = Depends(require_login)):
    """查看企微重导前的旧版本（存于 knowledge_base_old/）。"""
    safe_name = Path(file_name).name
    if not safe_name.endswith(".md"):
        raise HTTPException(400, "非法文件名")
    old_dir = Path(load_settings().knowledge_base_dir).parent / "knowledge_base_old"
    fpath = old_dir / safe_name
    if not fpath.exists():
        raise HTTPException(404, "无旧版本")
    return _render_md_html(safe_name + "（旧版·重导前）", fpath.read_text(encoding="utf-8"))


def _render_md_html(title_src: str, content: str):
    from fastapi.responses import HTMLResponse
    import html as _html
    if not content:
        raise HTTPException(404, "文档不存在")
    title = _html.escape(title_src.replace(".md", ""))
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5/github-markdown-light.min.css">
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
<style>
body {{ max-width: 900px; margin: 40px auto; padding: 0 20px; background: #fff; }}
.markdown-body {{ font-size: 15px; line-height: 1.7; }}
.markdown-body img {{ max-width: 100%; height: auto; }}
</style>
</head>
<body>
<article class="markdown-body" id="content"></article>
<script>
const raw = {repr(content)};
// 不可信内容(上传/抓取)：marked 渲染后用 DOMPurify 净化再写入，防 XSS
const dirty = marked.parse(raw);
document.getElementById('content').innerHTML = DOMPurify.sanitize(dirty, {{ ADD_ATTR: ['target','rel'] }});
document.querySelectorAll('#content a[href]').forEach(a => {{ a.target='_blank'; a.rel='noopener noreferrer'; }});
</script>
</body>
</html>"""
    return HTMLResponse(html_doc)


@router.get("/documents/{doc_id}")
async def get_document(doc_id: int, user: dict = Depends(require_login)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        doc = dict(row)
        content = read_file_content(doc["stored_path"])
        doc["content"] = content
        return doc


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int, request: Request):
    await _require_admin(request)
    kb_dir = Path(load_settings().knowledge_base_dir)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        doc = dict(row)
        file_path = kb_dir / doc["stored_path"]
        if file_path.exists():
            file_path.unlink()
        await db.execute("DELETE FROM document_tags WHERE document_id = ?", (doc_id,))
        await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        await db.commit()
    return {"message": "删除成功"}


class TagRequest(BaseModel):
    name: str
    description: str | None = None


class DocTagsRequest(BaseModel):
    tag_ids: list[int] = []


@router.get("/tags")
async def list_tags(user: dict = Depends(require_login)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT t.id, t.name, t.description, COUNT(dt.document_id) AS doc_count "
            "FROM tags t LEFT JOIN document_tags dt ON t.id = dt.tag_id "
            "GROUP BY t.id, t.name, t.description ORDER BY t.name"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


@router.post("/tags")
async def create_tag(req: TagRequest, request: Request):
    await _require_admin(request)
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")
    desc = (req.description or "").strip() or None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, name, description FROM tags WHERE name = ?", (name,)
        )
        existing = await cursor.fetchone()
        if existing:
            return {
                "id": existing["id"],
                "name": existing["name"],
                "description": existing["description"],
            }
        cursor = await db.execute(
            "INSERT INTO tags (name, description) VALUES (?, ?)", (name, desc)
        )
        await db.commit()
        return {"id": cursor.lastrowid, "name": name, "description": desc}


@router.put("/tags/{tag_id}")
async def rename_tag(tag_id: int, req: TagRequest, request: Request):
    await _require_admin(request)
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")
    desc = (req.description or "").strip() or None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id FROM tags WHERE name = ? AND id != ?", (name, tag_id)
        )
        if await cursor.fetchone():
            raise HTTPException(409, "标签名已存在")
        await db.execute(
            "UPDATE tags SET name = ?, description = ? WHERE id = ?",
            (name, desc, tag_id),
        )
        await db.commit()
    return {"id": tag_id, "name": name, "description": desc}


@router.delete("/tags/{tag_id}")
async def delete_tag(tag_id: int, request: Request):
    await _require_admin(request)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM document_tags WHERE tag_id = ?", (tag_id,))
        await db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        await db.commit()
    return {"message": "删除成功"}


@router.put("/documents/{doc_id}/tags")
async def set_document_tags(doc_id: int, req: DocTagsRequest, request: Request):
    await _require_admin(request)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id FROM documents WHERE id = ?", (doc_id,))
        if not await cursor.fetchone():
            raise HTTPException(404, "文档不存在")
        await db.execute("DELETE FROM document_tags WHERE document_id = ?", (doc_id,))
        unique_ids = list(dict.fromkeys(req.tag_ids))
        for tag_id in unique_ids:
            await db.execute(
                "INSERT INTO document_tags (document_id, tag_id) VALUES (?, ?)",
                (doc_id, tag_id),
            )
        await db.commit()
    return {"message": "标签已更新", "tag_ids": unique_ids}


class SettingsUpdate(BaseModel):
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_api_format: str = "openai"


@router.get("/settings")
async def get_settings(user: dict = Depends(require_admin)):
    s = load_settings()
    return {
        "llm_api_key": s.llm_api_key[:8] + "***" if len(s.llm_api_key) > 8 else "",
        "llm_base_url": s.llm_base_url,
        "llm_model": s.llm_model,
        "llm_api_format": s.llm_api_format,
    }


@router.put("/settings")
async def update_settings(data: SettingsUpdate, user: dict = Depends(require_admin)):
    s = load_settings()
    # 不用空 key 覆盖已有 key(前端回显的是掩码***，提交空表示不改)
    if data.llm_api_key and "***" not in data.llm_api_key:
        s.llm_api_key = data.llm_api_key
    s.llm_base_url = data.llm_base_url
    s.llm_model = data.llm_model
    s.llm_api_format = data.llm_api_format
    save_settings(s)
    return {"message": "设置已保存"}
