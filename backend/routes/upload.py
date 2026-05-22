import shutil
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
import aiosqlite
from config import load_settings
from parsers import parse_file, PARSERS
from database import DB_PATH
from scraper import scrape_tencent_doc, scrape_tencent_doc_recursive, validate_tencent_doc_url

router = APIRouter(tags=["upload"])
ALLOWED_EXTENSIONS = set(PARSERS.keys())
_executor = ThreadPoolExecutor(max_workers=2)


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件格式: {ext}，支持: {', '.join(ALLOWED_EXTENSIONS)}")

    kb_dir = Path(load_settings().knowledge_base_dir)
    kb_dir.mkdir(parents=True, exist_ok=True)
    temp_path = kb_dir / f"_temp_{file.filename}"
    try:
        with open(temp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        content = parse_file(str(temp_path))
        stored_name = Path(file.filename).stem + ".md"
        stored_path = kb_dir / stored_name

        counter = 1
        while stored_path.exists():
            stored_name = f"{Path(file.filename).stem}_{counter}.md"
            stored_path = kb_dir / stored_name
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


class UrlImportRequest(BaseModel):
    url: str
    recursive: bool = True
    max_depth: int = 5


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup: strip query params that don't identify the doc."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}{parsed.path}"


async def _is_url_imported(url: str) -> bool:
    """Check if a URL has already been imported."""
    norm = _normalize_url(url)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM documents WHERE source_url = ?", (norm,)
        )
        row = await cursor.fetchone()
        return row is not None


def _run_scraper(url: str) -> tuple[str, str]:
    """Run playwright scraper in a fresh event loop (avoids Windows ProactorEventLoop issues)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(scrape_tencent_doc(url))
    finally:
        loop.close()


def _run_scraper_recursive(url: str, max_depth: int) -> list[dict]:
    """Run recursive scraper in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(scrape_tencent_doc_recursive(url, max_depth=max_depth))
    finally:
        loop.close()


@router.post("/upload/url")
async def upload_from_url(req: UrlImportRequest):
    if not validate_tencent_doc_url(req.url):
        raise HTTPException(400, "不支持的链接，仅支持腾讯文档/企业微信文档（docs.qq.com / doc.weixin.qq.com）")

    if await _is_url_imported(req.url):
        raise HTTPException(409, "该文档已导入过，无需重复导入")

    loop = asyncio.get_event_loop()

    if not req.recursive:
        try:
            title, content = await loop.run_in_executor(_executor, _run_scraper, req.url)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except TimeoutError as e:
            raise HTTPException(504, str(e))
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(500, f"抓取失败: {type(e).__name__}: {str(e)}")

        stored_name = await _save_document(title, content, req.url, None)
        doc_entry = {"title": title, "stored_as": stored_name, "depth": 0, "url": req.url, "children": [], "parent": None, "error": None}

        tree_data = [doc_entry]
        await _save_import_tree(req.url, title, tree_data)

        return {
            "message": "导入成功",
            "documents": [doc_entry],
            "total": 1,
            "failed": [],
            "skipped": [],
        }

    try:
        results = await loop.run_in_executor(_executor, _run_scraper_recursive, req.url, req.max_depth)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"抓取失败: {type(e).__name__}: {str(e)}")

    documents = []
    failed = []
    skipped = []
    title_to_stored = {}

    for result in results:
        if result["error"]:
            failed.append({"url": result["url"], "error": result["error"], "depth": result["depth"]})
            continue

        if await _is_url_imported(result["url"]):
            skipped.append({"url": result["url"], "title": result["title"], "depth": result["depth"], "reason": "已导入"})
            title_to_stored[result["url"]] = {"title": result["title"], "stored_as": "(已存在)"}
            continue

        parent_title = None
        if result["parent_url"]:
            parent_title = title_to_stored.get(result["parent_url"], {}).get("title")

        child_titles = []
        for child_url in result["children"]:
            for r in results:
                if r["url"] == child_url and not r.get("error"):
                    child_titles.append(r["title"])
                    break

        content = _build_markdown_with_relations(
            result["title"], result["content"], result["url"],
            parent_title, child_titles
        )

        stored_name = await _save_document(result["title"], content, result["url"], parent_title)
        title_to_stored[result["url"]] = {"title": result["title"], "stored_as": stored_name}

        documents.append({
            "title": result["title"],
            "stored_as": stored_name,
            "depth": result["depth"],
            "url": result["url"],
            "children": child_titles,
            "parent": parent_title,
            "error": None,
        })

    all_entries = documents + [{"title": s["title"], "stored_as": "(已存在)", "depth": s["depth"], "url": s["url"], "children": [], "parent": None, "error": None} for s in skipped]
    root_title = title_to_stored.get(req.url, {}).get("title", "未知")
    await _save_import_tree(req.url, root_title, all_entries)

    msg_parts = [f"成功 {len(documents)} 个"]
    if skipped:
        msg_parts.append(f"跳过 {len(skipped)} 个(已存在)")
    if failed:
        msg_parts.append(f"失败 {len(failed)} 个")

    return {
        "message": f"导入完成，{'，'.join(msg_parts)}",
        "documents": documents,
        "total": len(documents),
        "failed": failed,
        "skipped": skipped,
    }


def _build_markdown_with_relations(
    title: str, text: str, url: str, parent_title: str | None, child_titles: list[str]
) -> str:
    header = f"# {title}\n\n> 来源: {url}\n"
    if parent_title:
        header += f"> 父文档: {parent_title}\n"
    if child_titles:
        header += f"> 子文档: {', '.join(child_titles)}\n"
    header += "\n"
    return header + text


async def _save_document(title: str, content: str, url: str, parent_title: str | None) -> str:
    kb_dir = Path(load_settings().knowledge_base_dir)
    kb_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{title}.md"
    stored_path = kb_dir / stored_name
    counter = 1
    while stored_path.exists():
        stored_name = f"{title}_{counter}.md"
        stored_path = kb_dir / stored_name
        counter += 1

    stored_path.write_text(content, encoding="utf-8")
    file_size = stored_path.stat().st_size

    source_label = f"{title} (腾讯文档)"
    if parent_title:
        source_label += f" [子文档 of {parent_title}]"

    norm_url = _normalize_url(url)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO documents (original_name, stored_path, file_type, file_size, source_url) VALUES (?, ?, ?, ?, ?)",
            (source_label, stored_name, ".url", file_size, norm_url),
        )
        await db.commit()

    return stored_name


async def _save_import_tree(root_url: str, root_title: str, tree_entries: list[dict]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO import_trees (root_url, root_title, tree_data, doc_count) VALUES (?, ?, ?, ?)",
            (root_url, root_title, json.dumps(tree_entries, ensure_ascii=False), len(tree_entries)),
        )
        await db.commit()


@router.get("/upload/trees")
async def list_import_trees():
    """List all import tree records."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, root_url, root_title, tree_data, doc_count, imported_at FROM import_trees ORDER BY imported_at DESC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "root_url": row["root_url"],
                "root_title": row["root_title"],
                "tree": json.loads(row["tree_data"]),
                "doc_count": row["doc_count"],
                "imported_at": row["imported_at"],
            }
            for row in rows
        ]
