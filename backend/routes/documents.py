from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import aiosqlite
from config import load_settings, save_settings
from database import DB_PATH
from auth import COOKIE_NAME
from searcher import read_file_content

router = APIRouter(tags=["documents"])


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
async def list_documents():
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
async def view_document_by_file(file_name: str):
    """View document content rendered as HTML with markdown styling."""
    from fastapi.responses import HTMLResponse
    content = read_file_content(file_name)
    if not content:
        raise HTTPException(404, "文档不存在")
    title = file_name.replace(".md", "")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5/github-markdown-light.min.css">
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
body {{ max-width: 900px; margin: 40px auto; padding: 0 20px; background: #fff; }}
.markdown-body {{ font-size: 15px; line-height: 1.7; }}
</style>
</head>
<body>
<article class="markdown-body" id="content"></article>
<script>
const raw = {repr(content)};
document.getElementById('content').innerHTML = marked.parse(raw);
</script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/documents/{doc_id}")
async def get_document(doc_id: int):
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
async def delete_document(doc_id: int):
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
async def list_tags():
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
async def get_settings():
    s = load_settings()
    return {
        "llm_api_key": s.llm_api_key[:8] + "***" if len(s.llm_api_key) > 8 else "",
        "llm_base_url": s.llm_base_url,
        "llm_model": s.llm_model,
        "llm_api_format": s.llm_api_format,
    }


@router.put("/settings")
async def update_settings(data: SettingsUpdate):
    s = load_settings()
    s.llm_api_key = data.llm_api_key
    s.llm_base_url = data.llm_base_url
    s.llm_model = data.llm_model
    s.llm_api_format = data.llm_api_format
    save_settings(s)
    return {"message": "设置已保存"}
