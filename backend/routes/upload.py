import os
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
import aiosqlite
from config import load_settings
from parsers import parse_file, PARSERS
from database import DB_PATH

router = APIRouter(tags=["upload"])
settings = load_settings()
KB_DIR = Path(settings.knowledge_base_dir)
ALLOWED_EXTENSIONS = set(PARSERS.keys())


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件格式: {ext}，支持: {', '.join(ALLOWED_EXTENSIONS)}")

    KB_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = KB_DIR / f"_temp_{file.filename}"
    try:
        with open(temp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        content = parse_file(str(temp_path))
        stored_name = Path(file.filename).stem + ".md"
        stored_path = KB_DIR / stored_name

        counter = 1
        while stored_path.exists():
            stored_name = f"{Path(file.filename).stem}_{counter}.md"
            stored_path = KB_DIR / stored_name
            counter += 1

        stored_path.write_text(content, encoding="utf-8")
        file_size = stored_path.stat().st_size

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO documents (original_name, stored_path, file_type, file_size) VALUES (?, ?, ?, ?)",
                (file.filename, stored_name, ext, file_size),
            )
            await db.commit()

    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {"message": "上传成功", "original_name": file.filename, "stored_as": stored_name}
