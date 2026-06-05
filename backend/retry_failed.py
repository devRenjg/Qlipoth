"""Batch-retry failed_imports using the fixed scraper.

For each failed record:
  - scrape via the shared logged-in browser context
  - success  -> save .md + insert into documents, delete failed_imports row
  - login/permission error -> keep the record (mark error)
  - other error -> update error + retry_count
Prints progress per item and a final summary.
"""
import asyncio
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

import scraper
from scraper import (
    _scrape_single_in_context, _sanitize_doc_url,
    BROWSER_DATA_DIR, CHROME_PATH,
)
from playwright.async_api import async_playwright
from config import load_settings
from datetime import datetime, timezone, timedelta

_BJ = timezone(timedelta(hours=8))
DB = load_settings().db_path
KB = Path(load_settings().knowledge_base_dir)

PERM_KEYWORDS = ("需要登录", "登录", "无权", "权限", "扫码", "验证身份")


def now_bj():
    return datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")


def norm_url(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.hostname}{p.path}"


def is_imported(con, url):
    return con.execute("SELECT 1 FROM documents WHERE source_url=?", (norm_url(url),)).fetchone() is not None


def save_doc(con, title, content, url):
    KB.mkdir(parents=True, exist_ok=True)
    stem = title or "未命名文档"
    name = f"{stem}.md"
    path = KB / name
    c = 1
    while path.exists():
        name = f"{stem}_{c}.md"
        path = KB / name
        c += 1
    path.write_text(content, encoding="utf-8")
    size = path.stat().st_size
    con.execute(
        "INSERT INTO documents (original_name, stored_path, file_type, file_size, source_url, uploaded_at) VALUES (?,?,?,?,?,?)",
        (f"{stem} (腾讯文档)", name, ".url", size, norm_url(url), now_bj()),
    )
    con.commit()
    return name


async def main():
    scraper.BROWSER_DATA_DIR = Path(__file__).parent.parent / ".browser_data"
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id, url, title, error, retry_count FROM failed_imports ORDER BY id").fetchall()
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if limit:
        rows = rows[:limit]
    total = len(rows)
    print(f"[retry] {total} failed records to process\n")

    ok, perm, skip, still_fail = 0, 0, 0, 0

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(scraper.BROWSER_DATA_DIR),
            headless=False,
            executable_path=CHROME_PATH,
            viewport={"width": 1920, "height": 1080},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            for i, r in enumerate(rows, 1):
                rid, url = r["id"], r["url"]
                tag = f"[{i}/{total}] id={rid}"
                if is_imported(con, url):
                    con.execute("DELETE FROM failed_imports WHERE id=?", (rid,))
                    con.commit()
                    skip += 1
                    print(f"{tag} SKIP already-imported -> removed record")
                    continue

                try:
                    res = await _scrape_single_in_context(ctx, url, timeout_ms=90000)
                except Exception as e:
                    res = {"error": f"{type(e).__name__}: {e}", "title": "", "content": ""}

                err = res.get("error")
                if not err and res.get("content"):
                    title = res["title"] or "未命名文档"
                    content = f"# {title}\n\n> 来源: {url}\n\n{res['content']}"
                    name = save_doc(con, title, content, url)
                    con.execute("DELETE FROM failed_imports WHERE id=?", (rid,))
                    con.commit()
                    ok += 1
                    print(f"{tag} OK  {title!r} len={len(res['content'])} -> {name}")
                elif err and any(k in err for k in PERM_KEYWORDS):
                    con.execute(
                        "UPDATE failed_imports SET error=?, retry_count=retry_count+1, last_retry=? WHERE id=?",
                        (f"[权限] {err}", now_bj(), rid),
                    )
                    con.commit()
                    perm += 1
                    print(f"{tag} PERM keep -> {err}")
                else:
                    con.execute(
                        "UPDATE failed_imports SET error=?, retry_count=retry_count+1, last_retry=? WHERE id=?",
                        (err or "内容为空", now_bj(), rid),
                    )
                    con.commit()
                    still_fail += 1
                    print(f"{tag} FAIL -> {err}")
        finally:
            await ctx.close()

    print(f"\n[retry] done. OK={ok} PERM(keep)={perm} SKIP={skip} FAIL={still_fail} total={total}")
    remaining = con.execute("SELECT COUNT(*) FROM failed_imports").fetchone()[0]
    print(f"[retry] failed_imports remaining: {remaining}")
    con.close()


if __name__ == "__main__":
    asyncio.run(main())
