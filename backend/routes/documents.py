from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import aiosqlite
from config import load_settings, save_settings
from database import DB_PATH
from searcher import read_file_content

router = APIRouter(tags=["documents"])


@router.get("/documents")
async def list_documents():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM documents ORDER BY uploaded_at DESC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


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
        await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        await db.commit()
    return {"message": "删除成功"}


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
