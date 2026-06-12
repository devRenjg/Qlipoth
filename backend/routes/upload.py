import shutil
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import aiosqlite
from config import load_settings
from parsers import parse_file, PARSERS, extract_owners
from database import DB_PATH
from scraper import scrape_tencent_doc, scrape_tencent_doc_recursive, validate_tencent_doc_url
from confluence import (
    scrape_confluence_recursive,
    validate_confluence_url,
    ConfluenceAuthError,
)
from tagger import tag_document


def _url_kind(url: str) -> str | None:
    """Classify an import URL. Returns 'tencent', 'confluence', or None."""
    if validate_tencent_doc_url(url):
        return "tencent"
    if validate_confluence_url(url):
        return "confluence"
    return None


# 在线文档来源标签：用于 documents.original_name 后缀与提示文案
_SOURCE_NAME = {"tencent": "腾讯文档", "confluence": "Confluence"}


def _parse_tags(raw: str | list[str] | None) -> list[str]:
    """Accept JSON array string, comma-separated string, or list. Return clean name list."""
    if not raw:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        s = raw.strip()
        if s.startswith("["):
            try:
                items = json.loads(s)
            except json.JSONDecodeError:
                items = s.split(",")
        else:
            items = s.split(",")
    return [str(t).strip() for t in items if str(t).strip()]


# 活动维度标签：随导入树传递给所有子文档（同一棵树属于同一活动）
ACTIVITY_TAGS = {"S赛", "跨晚", "春晚", "事故/故障"}


def _activity_tags(tags: list[str] | None) -> list[str]:
    """从用户手选标签里筛出活动维度标签，供子文档继承。"""
    return [t for t in (tags or []) if t in ACTIVITY_TAGS]


router = APIRouter(tags=["upload"])
ALLOWED_EXTENSIONS = set(PARSERS.keys())
_executor = ThreadPoolExecutor(max_workers=2)
_BJ_TZ = timezone(timedelta(hours=8))


def _now_bj() -> str:
    return datetime.now(_BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), tags: str = Form(default="")):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件格式: {ext}，支持: {', '.join(ALLOWED_EXTENSIONS)}")

    manual_tags = _parse_tags(tags)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM documents WHERE original_name = ?", (file.filename,)
        )
        if await cursor.fetchone():
            raise HTTPException(409, f"文件「{file.filename}」已导入过，无需重复导入")

    kb_dir = Path(load_settings().knowledge_base_dir)
    kb_dir.mkdir(parents=True, exist_ok=True)
    temp_path = kb_dir / f"_temp_{file.filename}"
    try:
        with open(temp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        content = parse_file(str(temp_path))
        owners = extract_owners(content)
        if owners:
            content = f"> 负责人: {', '.join('@' + o for o in owners)}\n\n{content}"
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
            cursor = await db.execute(
                "INSERT INTO documents (original_name, stored_path, file_type, file_size, uploaded_at) VALUES (?, ?, ?, ?, ?)",
                (file.filename, stored_name, ext, file_size, _now_bj()),
            )
            await db.commit()
            doc_id = cursor.lastrowid

        applied_tags = await tag_document(doc_id, file.filename, content, manual_tags)

        tree_entry = [{"title": file.filename, "stored_as": stored_name, "depth": 0, "url": "", "children": [], "parent": None, "error": None}]
        await _save_import_tree(f"file://{file.filename}", file.filename, tree_entry)

    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {"message": "上传成功", "original_name": file.filename, "stored_as": stored_name, "tags": applied_tags}


class UrlImportRequest(BaseModel):
    url: str
    max_depth: int = 2
    tags: list[str] = []

    @property
    def recursive(self) -> bool:
        return self.max_depth > 0


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup: strip query params that don't identify the doc.

    Confluence pages are identified by pageId, which lives in the query string
    (…/pages/viewpage.action?pageId=NNN). Stripping the whole query would
    collapse every Confluence page to the same key, so we keep pageId for those.
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
    qs = parse_qs(parsed.query)
    if "pageId" in qs and qs["pageId"]:
        return f"{base}?pageId={qs['pageId'][0]}"
    return base


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
    if req.max_depth < 0 or req.max_depth > 3:
        raise HTTPException(400, "递归层数范围为 0-3（0 表示不递归）")

    kind = _url_kind(req.url)
    if kind is None:
        raise HTTPException(400, "不支持的链接，仅支持腾讯文档/企业微信文档（docs.qq.com / doc.weixin.qq.com）或 Confluence（wiki.example.com）")

    loop = asyncio.get_event_loop()

    if kind == "confluence":
        return await _import_confluence_batch(req)

    if not req.recursive:
        if await _is_url_imported(req.url):
            raise HTTPException(409, "该文档已导入过，无需重复导入")
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

        stored_name, doc_id = await _save_document(title, content, req.url, None)
        await tag_document(doc_id, title, content, req.tags)
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

        stored_name, doc_id = await _save_document(result["title"], content, result["url"], parent_title)
        # 根文档用全部手选标签；子文档继承活动标签(同树同活动)，主题标签按内容自动补
        doc_manual = req.tags if result["depth"] == 0 else _activity_tags(req.tags)
        await tag_document(doc_id, result["title"], content, doc_manual)
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


@router.post("/upload/url/stream")
async def upload_from_url_stream(req: UrlImportRequest):
    """SSE stream version of recursive URL import, pushing progress per document."""
    if req.max_depth < 0 or req.max_depth > 3:
        raise HTTPException(400, "递归层数范围为 0-3（0 表示不递归）")

    kind = _url_kind(req.url)
    if kind is None:
        raise HTTPException(400, "不支持的链接，仅支持腾讯文档/企业微信文档（docs.qq.com / doc.weixin.qq.com）或 Confluence（wiki.example.com）")

    if kind == "confluence":
        return await _stream_confluence_import(req)

    # max_depth=0 时不递归，父文档已导入则直接拒绝
    if not req.recursive:
        if await _is_url_imported(req.url):
            raise HTTPException(409, "该文档已导入过，无需重复导入")
        loop = asyncio.get_event_loop()
        try:
            title, content = await loop.run_in_executor(_executor, _run_scraper, req.url)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except TimeoutError as e:
            raise HTTPException(504, str(e))
        except Exception as e:
            raise HTTPException(500, f"抓取失败: {type(e).__name__}: {str(e)}")

        stored_name, doc_id = await _save_document(title, content, req.url, None)
        await tag_document(doc_id, title, content, req.tags)
        doc_entry = {"title": title, "stored_as": stored_name, "depth": 0, "url": req.url, "children": [], "parent": None, "parent_url": None, "error": None}
        await _save_import_tree(req.url, title, [doc_entry])

        async def single_gen():
            yield f"data: {json.dumps({'type': 'progress', 'data': {'status': 'success', 'title': title, 'depth': 0, 'url': req.url}}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'data': {'message': '导入成功', 'total': 1, 'success': 1, 'failed': 0, 'skipped': 0}}, ensure_ascii=False)}\n\n"

        return StreamingResponse(single_gen(), media_type="text/event-stream")

    # 递归模式：父文档已导入也继续，只跳过已导入的单个文档，子文档照常录入
    progress_queue = asyncio.Queue()
    main_loop = asyncio.get_event_loop()

    def _run_recursive_with_progress():
        """Run in a thread with its own event loop to avoid Windows ProactorEventLoop issues."""
        loop = asyncio.new_event_loop()

        async def _inner():
            documents = []
            failed = []
            skipped = []
            title_to_stored = {}

            async def on_progress(result):
                if result["error"]:
                    failed.append(result)
                    # 持久化失败记录
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "INSERT INTO failed_imports (url, title, error, parent_url, depth, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (result["url"], result.get("title", ""), result["error"], result.get("parent_url"), result.get("depth", 0), _now_bj()),
                        )
                        await db.commit()
                    main_loop.call_soon_threadsafe(progress_queue.put_nowait, {
                        "type": "progress",
                        "data": {"status": "failed", "title": result.get("title") or result["url"], "depth": result["depth"], "url": result["url"], "error": result["error"]},
                    })
                    return

                norm = _normalize_url(result["url"])
                async with aiosqlite.connect(DB_PATH) as db:
                    cursor = await db.execute("SELECT id FROM documents WHERE source_url = ?", (norm,))
                    already = await cursor.fetchone()

                if already:
                    skipped.append(result)
                    title_to_stored[result["url"]] = {"title": result["title"], "stored_as": "(已存在)"}
                    main_loop.call_soon_threadsafe(progress_queue.put_nowait, {
                        "type": "progress",
                        "data": {"status": "skipped", "title": result["title"], "depth": result["depth"], "url": result["url"], "reason": "已导入"},
                    })
                    return

                parent_title = None
                parent_url = result.get("parent_url")
                if parent_url:
                    parent_title = title_to_stored.get(parent_url, {}).get("title")

                child_urls = result.get("children", [])
                content = _build_markdown_with_relations(
                    result["title"], result["content"], result["url"],
                    parent_title, []
                )

                kb_dir = Path(load_settings().knowledge_base_dir)
                kb_dir.mkdir(parents=True, exist_ok=True)
                stored_name = f"{result['title']}.md"
                stored_path = kb_dir / stored_name
                counter = 1
                while stored_path.exists():
                    stored_name = f"{result['title']}_{counter}.md"
                    stored_path = kb_dir / stored_name
                    counter += 1
                stored_path.write_text(content, encoding="utf-8")
                file_size = stored_path.stat().st_size

                source_label = f"{result['title']} (腾讯文档)"
                if parent_title:
                    source_label += f" [子文档 of {parent_title}]"
                norm_url = _normalize_url(result["url"])

                async with aiosqlite.connect(DB_PATH) as db:
                    cursor = await db.execute(
                        "INSERT INTO documents (original_name, stored_path, file_type, file_size, source_url, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (source_label, stored_name, ".url", file_size, norm_url, _now_bj()),
                    )
                    await db.commit()
                    doc_id = cursor.lastrowid

                # 根文档用全部手选标签；子文档继承活动标签，主题标签按内容自动补
                try:
                    doc_manual = req.tags if result["depth"] == 0 else _activity_tags(req.tags)
                    await tag_document(doc_id, result["title"], content, doc_manual)
                except Exception:
                    pass

                title_to_stored[result["url"]] = {"title": result["title"], "stored_as": stored_name}
                documents.append({
                    "title": result["title"], "stored_as": stored_name,
                    "depth": result["depth"], "url": result["url"],
                    "children": child_urls, "parent": parent_title,
                    "parent_url": parent_url, "error": None,
                })
                main_loop.call_soon_threadsafe(progress_queue.put_nowait, {
                    "type": "progress",
                    "data": {"status": "success", "title": result["title"], "depth": result["depth"], "url": result["url"], "stored_as": stored_name},
                })

            try:
                await scrape_tencent_doc_recursive(req.url, max_depth=req.max_depth, on_progress=on_progress)
            except Exception as e:
                import traceback
                traceback.print_exc()
                main_loop.call_soon_threadsafe(progress_queue.put_nowait, {"type": "error", "data": {"error": f"{type(e).__name__}: {str(e)}"}})
                return

            # 构建完整的树形记录
            all_entries = []
            for doc in documents:
                all_entries.append({
                    "title": doc["title"], "stored_as": doc["stored_as"],
                    "depth": doc["depth"], "url": doc["url"],
                    "children": doc["children"], "parent": doc["parent"],
                    "parent_url": doc.get("parent_url"), "error": None,
                })
            for r in skipped:
                all_entries.append({
                    "title": r.get("title", ""), "stored_as": "(已存在)",
                    "depth": r["depth"], "url": r["url"],
                    "children": r.get("children", []), "parent": title_to_stored.get(r.get("parent_url"), {}).get("title"),
                    "parent_url": r.get("parent_url"), "error": None,
                })
            for r in failed:
                all_entries.append({
                    "title": r.get("title", ""), "stored_as": None,
                    "depth": r["depth"], "url": r["url"],
                    "children": [], "parent": title_to_stored.get(r.get("parent_url"), {}).get("title"),
                    "parent_url": r.get("parent_url"), "error": r["error"],
                })

            root_title = title_to_stored.get(req.url, {}).get("title") or "未知"
            all_entries.sort(key=lambda x: x["depth"])
            if all_entries:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT INTO import_trees (root_url, root_title, tree_data, doc_count, imported_at) VALUES (?, ?, ?, ?, ?)",
                        (req.url, root_title, json.dumps(all_entries, ensure_ascii=False), len(all_entries), _now_bj()),
                    )
                    await db.commit()

            main_loop.call_soon_threadsafe(progress_queue.put_nowait, {
                "type": "done",
                "data": {"message": "导入完成", "total": len(all_entries), "success": len(documents), "failed": len(failed), "skipped": len(skipped)},
            })

        try:
            loop.run_until_complete(_inner())
        except Exception as e:
            import traceback
            traceback.print_exc()
            main_loop.call_soon_threadsafe(progress_queue.put_nowait, {"type": "error", "data": {"error": f"{type(e).__name__}: {str(e)}"}})
        finally:
            loop.close()

    async def event_generator():
        _executor.submit(_run_recursive_with_progress)
        while True:
            msg = await progress_queue.get()
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            if msg["type"] in ("done", "error"):
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def _stream_confluence_import(req: "UrlImportRequest"):
    """SSE import for an wiki.example.com page and ALL its descendant pages.

    Confluence exposes a real page tree, so depth is ignored — we always fetch
    the root plus every descendant. Reuses the same落盘/写库/打标/导入树 path
    as the Tencent flow.
    """
    progress_queue = asyncio.Queue()
    main_loop = asyncio.get_event_loop()

    def _run():
        loop = asyncio.new_event_loop()

        async def _inner():
            documents, failed, skipped = [], [], []
            title_to_stored: dict[str, dict] = {}

            async def ingest(result: dict):
                if result["error"]:
                    failed.append(result)
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "INSERT INTO failed_imports (url, title, error, parent_url, depth, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (result["url"], result.get("title", ""), result["error"], result.get("parent_url"), result.get("depth", 0), _now_bj()),
                        )
                        await db.commit()
                    main_loop.call_soon_threadsafe(progress_queue.put_nowait, {
                        "type": "progress",
                        "data": {"status": "failed", "title": result.get("title") or result["url"], "depth": result["depth"], "url": result["url"], "error": result["error"]},
                    })
                    return

                norm = _normalize_url(result["url"])
                async with aiosqlite.connect(DB_PATH) as db:
                    cursor = await db.execute("SELECT id FROM documents WHERE source_url = ?", (norm,))
                    already = await cursor.fetchone()
                if already:
                    skipped.append(result)
                    title_to_stored[result["url"]] = {"title": result["title"], "stored_as": "(已存在)"}
                    main_loop.call_soon_threadsafe(progress_queue.put_nowait, {
                        "type": "progress",
                        "data": {"status": "skipped", "title": result["title"], "depth": result["depth"], "url": result["url"], "reason": "已导入"},
                    })
                    return

                parent_url = result.get("parent_url")
                parent_title = title_to_stored.get(parent_url, {}).get("title") if parent_url else None
                content = _build_markdown_with_relations(result["title"], result["content"], result["url"], parent_title, [])
                stored_name, doc_id = await _save_document(result["title"], content, result["url"], parent_title, source="confluence")

                try:
                    doc_manual = req.tags if result["depth"] == 0 else _activity_tags(req.tags)
                    await tag_document(doc_id, result["title"], content, doc_manual)
                except Exception:
                    pass

                title_to_stored[result["url"]] = {"title": result["title"], "stored_as": stored_name}
                documents.append({
                    "title": result["title"], "stored_as": stored_name,
                    "depth": result["depth"], "url": result["url"],
                    "children": result.get("children", []), "parent": parent_title,
                    "parent_url": parent_url, "error": None,
                })
                main_loop.call_soon_threadsafe(progress_queue.put_nowait, {
                    "type": "progress",
                    "data": {"status": "success", "title": result["title"], "depth": result["depth"], "url": result["url"], "stored_as": stored_name},
                })

            # scrape_confluence_recursive is sync (REST API); run it in this
            # worker thread, then ingest each result in order.
            try:
                from confluence import scrape_confluence_recursive as _scrape
                results = _scrape(req.url)
            except ConfluenceAuthError as e:
                main_loop.call_soon_threadsafe(progress_queue.put_nowait, {"type": "error", "data": {"error": str(e)}})
                return
            except Exception as e:
                import traceback
                traceback.print_exc()
                main_loop.call_soon_threadsafe(progress_queue.put_nowait, {"type": "error", "data": {"error": f"{type(e).__name__}: {str(e)}"}})
                return

            for result in results:
                await ingest(result)

            all_entries = []
            for doc in documents:
                all_entries.append({k: doc.get(k) for k in ("title", "stored_as", "depth", "url", "children", "parent", "parent_url")} | {"error": None})
            for r in skipped:
                all_entries.append({"title": r.get("title", ""), "stored_as": "(已存在)", "depth": r["depth"], "url": r["url"], "children": r.get("children", []), "parent": title_to_stored.get(r.get("parent_url"), {}).get("title"), "parent_url": r.get("parent_url"), "error": None})
            for r in failed:
                all_entries.append({"title": r.get("title", ""), "stored_as": None, "depth": r["depth"], "url": r["url"], "children": [], "parent": title_to_stored.get(r.get("parent_url"), {}).get("title"), "parent_url": r.get("parent_url"), "error": r["error"]})

            root_title = title_to_stored.get(req.url, {}).get("title") or "未知"
            all_entries.sort(key=lambda x: x["depth"])
            if all_entries:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT INTO import_trees (root_url, root_title, tree_data, doc_count, imported_at) VALUES (?, ?, ?, ?, ?)",
                        (req.url, root_title, json.dumps(all_entries, ensure_ascii=False), len(all_entries), _now_bj()),
                    )
                    await db.commit()

            main_loop.call_soon_threadsafe(progress_queue.put_nowait, {
                "type": "done",
                "data": {"message": "导入完成", "total": len(all_entries), "success": len(documents), "failed": len(failed), "skipped": len(skipped)},
            })

        try:
            loop.run_until_complete(_inner())
        except Exception as e:
            import traceback
            traceback.print_exc()
            main_loop.call_soon_threadsafe(progress_queue.put_nowait, {"type": "error", "data": {"error": f"{type(e).__name__}: {str(e)}"}})
        finally:
            loop.close()

    async def event_generator():
        _executor.submit(_run)
        while True:
            msg = await progress_queue.get()
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            if msg["type"] in ("done", "error"):
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def _import_confluence_batch(req: "UrlImportRequest"):
    """Non-stream JSON import of a Confluence page + all descendants."""
    loop = asyncio.get_event_loop()
    try:
        from confluence import scrape_confluence_recursive as _scrape
        results = await loop.run_in_executor(_executor, _scrape, req.url)
    except ConfluenceAuthError as e:
        raise HTTPException(403, str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"抓取失败: {type(e).__name__}: {str(e)}")

    documents, failed, skipped = [], [], []
    title_to_stored: dict[str, dict] = {}

    for result in results:
        if result["error"]:
            failed.append({"url": result["url"], "error": result["error"], "depth": result["depth"]})
            continue
        if await _is_url_imported(result["url"]):
            skipped.append({"url": result["url"], "title": result["title"], "depth": result["depth"], "reason": "已导入"})
            title_to_stored[result["url"]] = {"title": result["title"], "stored_as": "(已存在)"}
            continue

        parent_url = result.get("parent_url")
        parent_title = title_to_stored.get(parent_url, {}).get("title") if parent_url else None
        content = _build_markdown_with_relations(result["title"], result["content"], result["url"], parent_title, [])
        stored_name, doc_id = await _save_document(result["title"], content, result["url"], parent_title, source="confluence")
        doc_manual = req.tags if result["depth"] == 0 else _activity_tags(req.tags)
        await tag_document(doc_id, result["title"], content, doc_manual)
        title_to_stored[result["url"]] = {"title": result["title"], "stored_as": stored_name}
        documents.append({
            "title": result["title"], "stored_as": stored_name, "depth": result["depth"],
            "url": result["url"], "children": result.get("children", []), "parent": parent_title, "error": None,
        })

    all_entries = documents + [{"title": s["title"], "stored_as": "(已存在)", "depth": s["depth"], "url": s["url"], "children": [], "parent": None, "error": None} for s in skipped]
    root_title = title_to_stored.get(req.url, {}).get("title", "未知")
    await _save_import_tree(req.url, root_title, all_entries)

    msg_parts = [f"成功 {len(documents)} 个"]
    if skipped:
        msg_parts.append(f"跳过 {len(skipped)} 个(已存在)")
    if failed:
        msg_parts.append(f"失败 {len(failed)} 个")
    return {"message": f"导入完成，{'，'.join(msg_parts)}", "documents": documents, "total": len(documents), "failed": failed, "skipped": skipped}


def _build_markdown_with_relations(
    title: str, text: str, url: str, parent_title: str | None, child_titles: list[str]
) -> str:
    owners = extract_owners(text)
    header = f"# {title}\n\n> 来源: {url}\n"
    if owners:
        header += f"> 负责人: {', '.join('@' + o for o in owners)}\n"
    if parent_title:
        header += f"> 父文档: {parent_title}\n"
    if child_titles:
        header += f"> 子文档: {', '.join(child_titles)}\n"
    header += "\n"
    return header + text


def _safe_filename(title: str) -> str:
    """Sanitize a doc title into a single-segment filename stem.

    Confluence titles may contain path separators and other characters illegal
    in Windows filenames (e.g. "S14业务降级/预案汇总"), which would otherwise be
    interpreted as nested directories or fail the write. Replace them with '_'.
    """
    stem = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", title).strip().strip(".")
    return stem or "未命名"


async def _save_document(title: str, content: str, url: str, parent_title: str | None, source: str = "tencent") -> tuple[str, int]:
    kb_dir = Path(load_settings().knowledge_base_dir)
    kb_dir.mkdir(parents=True, exist_ok=True)

    safe = _safe_filename(title)
    stored_name = f"{safe}.md"
    stored_path = kb_dir / stored_name
    counter = 1
    while stored_path.exists():
        stored_name = f"{safe}_{counter}.md"
        stored_path = kb_dir / stored_name
        counter += 1

    stored_path.write_text(content, encoding="utf-8")
    file_size = stored_path.stat().st_size

    source_label = f"{title} ({_SOURCE_NAME.get(source, '在线文档')})"
    if parent_title:
        source_label += f" [子文档 of {parent_title}]"

    norm_url = _normalize_url(url)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO documents (original_name, stored_path, file_type, file_size, source_url, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (source_label, stored_name, ".url", file_size, norm_url, _now_bj()),
        )
        await db.commit()
        doc_id = cursor.lastrowid

    return stored_name, doc_id


async def _save_import_tree(root_url: str, root_title: str, tree_entries: list[dict]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO import_trees (root_url, root_title, tree_data, doc_count, imported_at) VALUES (?, ?, ?, ?, ?)",
            (root_url, root_title, json.dumps(tree_entries, ensure_ascii=False), len(tree_entries), _now_bj()),
        )
        await db.commit()


@router.get("/upload/trees")
async def list_import_trees():
    """List all import tree records.

    附带从 tree_data 计算的成功/跳过/失败计数与展示标题兜底，
    便于前端导入历史一眼看清结果（修复 root_title='未知' 且只显示成功数的展示问题）。
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, root_url, root_title, tree_data, doc_count, imported_at FROM import_trees ORDER BY imported_at DESC"
        )
        rows = await cursor.fetchall()
        return [_tree_record(row) for row in rows]


def _tree_node_counts(tree: list[dict]) -> dict:
    """从扁平 tree_data 统计成功/跳过(已存在)/失败数。"""
    success = skipped = failed = 0
    for n in tree:
        if n.get("error"):
            failed += 1
        elif n.get("stored_as") == "(已存在)":
            skipped += 1
        elif n.get("stored_as"):
            success += 1
    return {"success": success, "skipped": skipped, "failed": failed, "total": len(tree)}


def _tree_display_title(root_title: str, tree: list[dict], root_url: str) -> str:
    """root_title 为'未知'/空时，回退到首个有标题的成功节点，仍无则用 URL。"""
    if root_title and root_title != "未知":
        return root_title
    for n in tree:
        t = (n.get("title") or "").strip()
        if t and not n.get("error"):
            return t
    return root_url or "未知来源"


def _tree_record(row) -> dict:
    tree = json.loads(row["tree_data"])
    counts = _tree_node_counts(tree)
    return {
        "id": row["id"],
        "root_url": row["root_url"],
        "root_title": row["root_title"],
        "display_title": _tree_display_title(row["root_title"], tree, row["root_url"]),
        "tree": tree,
        "doc_count": row["doc_count"],
        "counts": counts,
        "imported_at": row["imported_at"],
    }


@router.delete("/upload/trees/{tree_id}")
async def delete_import_tree(tree_id: int):
    """Delete an import tree record and its associated documents."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT tree_data FROM import_trees WHERE id = ?", (tree_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "导入记录不存在")

        tree_entries = json.loads(row["tree_data"])
        kb_dir = Path(load_settings().knowledge_base_dir)

        deleted_files = 0
        for entry in tree_entries:
            stored_as = entry.get("stored_as")
            if not stored_as or stored_as == "(已存在)":
                continue
            file_path = kb_dir / stored_as
            if file_path.exists():
                file_path.unlink()
                deleted_files += 1
            await db.execute("DELETE FROM documents WHERE stored_path = ?", (stored_as,))

        await db.execute("DELETE FROM import_trees WHERE id = ?", (tree_id,))
        await db.commit()

    return {"message": f"已删除，清理了 {deleted_files} 个文件"}


@router.get("/upload/failed")
async def list_failed_imports():
    """List all failed import records for future retry."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM failed_imports ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


@router.delete("/upload/failed/{record_id}")
async def delete_failed_import(record_id: int):
    """Delete a failed import record."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM failed_imports WHERE id = ?", (record_id,))
        await db.commit()
    return {"message": "已删除"}


class FailedReasonUpdate(BaseModel):
    error: str


@router.patch("/upload/failed/{record_id}")
async def update_failed_import_reason(record_id: int, req: FailedReasonUpdate):
    """Update the failure reason of a record after a retry fails again."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE failed_imports SET error = ?, created_at = ? WHERE id = ?",
            (req.error, _now_bj(), record_id),
        )
        await db.commit()
    return {"message": "已更新"}
