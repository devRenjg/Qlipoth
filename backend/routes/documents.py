from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import aiosqlite
from config import load_settings, save_settings, Settings
from database import DB_PATH
from searcher import read_file_content

router = APIRouter(tags=["documents"])
settings = load_settings()
KB_DIR = Path(settings.knowledge_base_dir)


@router.get("/documents")
async def list_documents():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM documents ORDER BY uploaded_at DESC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        doc = dict(row)
        file_path = KB_DIR / doc["stored_path"]
        if file_path.exists():
            file_path.unlink()
        await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        await db.commit()
    return {"message": "删除成功"}


class SettingsUpdate(BaseModel):
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"


@router.get("/settings")
async def get_settings():
    s = load_settings()
    return {
        "llm_api_key": s.llm_api_key[:8] + "***" if len(s.llm_api_key) > 8 else "",
        "llm_base_url": s.llm_base_url,
        "llm_model": s.llm_model,
    }


@router.put("/settings")
async def update_settings(data: SettingsUpdate):
    s = load_settings()
    s.llm_api_key = data.llm_api_key
    s.llm_base_url = data.llm_base_url
    s.llm_model = data.llm_model
    save_settings(s)
    return {"message": "设置已保存"}
