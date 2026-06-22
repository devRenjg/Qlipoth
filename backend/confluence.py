"""Confluence-style wiki page scraper (provider).

Unlike the Playwright-based online-doc scraper (discovers sub-docs from inline
links), a Confluence-style wiki exposes a real page tree via REST API, so we
fetch the root page plus ALL descendants directly. No headless browser needed.

配置(通过环境变量注入，公开仓库默认不含任何真实内部地址)：
- CONFLUENCE_BASE_URL : wiki 站点根 URL，如 https://wiki.example.com
- CONFLUENCE_COOKIE_FILE : 存放鉴权 cookie 的本地文件路径(默认 ~/.wiki_cookie)
未配置 CONFLUENCE_BASE_URL 时该 provider 不启用。
"""
import os
import re
import html as _html
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import httpx

BASE_URL = os.environ.get("CONFLUENCE_BASE_URL", "").rstrip("/")
ALLOWED_HOSTS = {urlparse(BASE_URL).hostname} if BASE_URL else set()
COOKIE_FILE = Path(os.environ.get("CONFLUENCE_COOKIE_FILE", str(Path.home() / ".wiki_cookie")))

try:
    import browser_cookie3  # noqa
    HAS_BROWSER_COOKIE3 = True
except ImportError:
    HAS_BROWSER_COOKIE3 = False


class ConfluenceError(Exception):
    pass


class ConfluenceAuthError(ConfluenceError):
    """Raised when no usable cookie can be obtained."""


def validate_confluence_url(url: str) -> bool:
    """True if url points to the configured wiki host."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and parsed.hostname in ALLOWED_HOSTS
    except Exception:
        return False


def extract_page_id(url_or_id: str) -> str:
    """Extract pageId from a Confluence URL or accept a bare numeric id."""
    s = (url_or_id or "").strip()
    if s.isdigit():
        return s
    parsed = urlparse(s)
    qs = parse_qs(parsed.query)
    if "pageId" in qs and qs["pageId"]:
        return qs["pageId"][0]
    # /display/SPACE/Title style has no pageId; /pages/NNN/ style
    m = re.search(r"/pages/(\d+)", parsed.path)
    if m:
        return m.group(1)
    raise ConfluenceError(f"无法从 '{url_or_id}' 提取 pageId（请使用含 pageId 的链接）")


_cookie_cache: dict[str, object] = {"value": None, "mtime": None}


def _get_cookie(force_browser: bool = False) -> str:
    """Cookie for the configured wiki host.

    Resolution order, fastest-first:
      1. In-memory cache (keyed on the cookie file's mtime, so editing the file
         invalidates it). This matters because the image proxy calls this once
         per image — without caching, many images × a multi-second browser read
         would block the event loop.
      2. Cookie file (instant, reliable) — path from CONFLUENCE_COOKIE_FILE.
      3. Browser (Chrome -> Edge) as a last resort. Chrome 127+ App-Bound
         Encryption makes this both slow and usually doomed, so it is NOT tried
         unless the file is absent.
    """
    auth_cookie = os.environ.get("CONFLUENCE_AUTH_COOKIE", "SESSIONID")
    host = next(iter(ALLOWED_HOSTS), "") if ALLOWED_HOSTS else ""
    # 1. file (preferred) + cache
    if COOKIE_FILE.exists():
        mtime = COOKIE_FILE.stat().st_mtime
        if _cookie_cache["value"] and _cookie_cache["mtime"] == mtime:
            return _cookie_cache["value"]
        cookie = COOKIE_FILE.read_text(encoding="utf-8").strip()
        if cookie:
            _cookie_cache["value"] = cookie
            _cookie_cache["mtime"] = mtime
            return cookie

    # 2. browser fallback (slow / often fails on Chrome 127+); only when no file
    if HAS_BROWSER_COOKIE3 and host:
        for name in ("chrome", "edge"):
            try:
                loader = getattr(browser_cookie3, name)
                cj = loader(domain_name=host)
                cookie = "; ".join(f"{c.name}={c.value}" for c in cj)
                if cookie and auth_cookie in cookie:
                    return cookie
            except Exception:
                continue

    raise ConfluenceAuthError(
        f"无法获取 wiki 登录态：请在浏览器登录后将 {auth_cookie} 写入 {COOKIE_FILE}"
        f"（内容：{auth_cookie}=xxx）"
    )


# ---------------------------------------------------------------------------
# Confluence storage-format HTML -> Markdown
# (ported from the bilisc-info skill so the backend has no skill dependency)
# ---------------------------------------------------------------------------

def _simple_html_to_text(html: str) -> str:
    text = html
    text = re.sub(r"<p[^>]*>", "", text)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<strong>([^<]*)</strong>", r"**\1**", text)
    text = re.sub(r"<em>([^<]*)</em>", r"*\1*", text)
    text = re.sub(r"<code[^>]*>([^<]*)</code>", r"`\1`", text)
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>', r"[\2](\1)", text)
    text = re.sub(r"<li[^>]*>", "- ", text)
    text = re.sub(r"</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return _html.unescape(text).strip()


def _convert_macro(match: "re.Match") -> str:
    macro_html = match.group(0)
    name_match = re.search(r'ac:name="([^"]+)"', macro_html)
    if not name_match:
        return ""
    macro_name = name_match.group(1).lower()

    params = {}
    for pm in re.finditer(r'<ac:parameter ac:name="([^"]+)"[^>]*>([^<]*)</ac:parameter>', macro_html):
        params[pm.group(1)] = pm.group(2)

    plain_match = re.search(r"<ac:plain-text-body>\s*<!\[CDATA\[(.*?)\]\]>\s*</ac:plain-text-body>", macro_html, re.DOTALL)
    plain_body = plain_match.group(1) if plain_match else ""
    rich_match = re.search(r"<ac:rich-text-body>(.*?)</ac:rich-text-body>", macro_html, re.DOTALL)
    rich_body = rich_match.group(1) if rich_match else ""

    if macro_name == "code":
        lang = params.get("language", "")
        result = f"\n```{lang}"
        if params.get("title"):
            result += f"  # {params['title']}"
        return result + f"\n{plain_body}\n```\n"
    if macro_name in ("info", "note", "warning", "tip"):
        body = _simple_html_to_text(rich_body)
        emoji = {"info": "ℹ️", "note": "📝", "warning": "⚠️", "tip": "💡"}.get(macro_name, "📌")
        result = f"\n> {emoji}"
        result += f" **{params['title']}**\n> " if params.get("title") else " "
        return result + "\n> ".join(body.strip().split("\n")) + "\n"
    if macro_name == "panel":
        body = _simple_html_to_text(rich_body)
        result = "\n"
        if params.get("title"):
            result += f"> **{params['title']}**\n"
        return result + "> " + "\n> ".join(body.strip().split("\n")) + "\n"
    if macro_name == "expand":
        body = _simple_html_to_text(rich_body)
        return f"\n<details>\n<summary>{params.get('title', 'Details')}</summary>\n\n{body}\n</details>\n"
    if macro_name == "markdown":
        return f"\n{plain_body}\n"
    if macro_name == "toc":
        return "\n[TOC]\n"
    if macro_name == "noformat":
        return f"\n```\n{plain_body}\n```\n"
    if macro_name == "quote":
        body = _simple_html_to_text(rich_body) if rich_body else plain_body
        return "\n> " + "\n> ".join(body.strip().split("\n")) + "\n"
    if macro_name in ("excerpt", "section", "column", "layout"):
        body = _simple_html_to_text(rich_body) if rich_body else plain_body
        return f"\n{body}\n"
    if macro_name in ("jira", "status", "recently-updated", "page-tree", "children", "attachments"):
        ps = ", ".join(f"{k}={v}" for k, v in params.items())
        return f"\n[{macro_name.upper()}: {ps}]\n"
    # generic fallback
    if plain_body:
        return f"\n```  <!-- {macro_name} macro -->\n{plain_body}\n```\n"
    if rich_body:
        body = _simple_html_to_text(rich_body)
        if body.strip():
            return f"\n<!-- {macro_name} macro -->\n{body}\n"
    return ""


def _convert_image(match: "re.Match", page_id: str = None) -> str:
    image_html = match.group(0)
    alt_match = re.search(r'ac:alt="([^"]*)"', image_html)
    alt = alt_match.group(1) if alt_match else ""
    att = re.search(r'<ri:attachment ri:filename="([^"]+)"', image_html)
    if att:
        filename = att.group(1)
        url = f"{BASE_URL}/download/attachments/{page_id}/{filename}" if page_id else filename
        return f"![{alt or filename}]({url})"
    urlm = re.search(r'<ri:url ri:value="([^"]+)"', image_html)
    if urlm:
        return f"![{alt}]({urlm.group(1)})"
    return ""


def html_to_markdown(html: str, page_id: str = None) -> str:
    """Convert Confluence storage-format HTML to Markdown."""
    content = html
    content = re.sub(r"<ac:image[^>]*>.*?</ac:image>", lambda m: _convert_image(m, page_id), content, flags=re.DOTALL)
    content = re.sub(r'<ac:structured-macro[^>]*ac:name="[^"]+"[^>]*>.*?</ac:structured-macro>', _convert_macro, content, flags=re.DOTALL)
    content = re.sub(r"<span[^>]*>", "", content)
    content = re.sub(r"</span>", "", content)
    content = re.sub(r"<p[^>]*>", "\n", content)
    content = re.sub(r"</p>", "", content)
    content = re.sub(r"<br\s*/?>", "\n", content)
    content = re.sub(r"<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", content)
    content = re.sub(r"</h[1-6]>", "\n", content)
    content = re.sub(r"<strong>", "**", content)
    content = re.sub(r"</strong>", "**", content)
    content = re.sub(r"<em>", "*", content)
    content = re.sub(r"</em>", "*", content)
    content = re.sub(r"<pre[^>]*>", "\n```\n", content)
    content = re.sub(r"</pre>", "\n```\n", content)
    content = re.sub(r"<code[^>]*>", "`", content)
    content = re.sub(r"</code>", "`", content)
    content = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>', r"[\2](\1)", content)
    content = re.sub(r"<li[^>]*>", "\n- ", content)
    content = re.sub(r"</li>", "", content)
    content = re.sub(r"<ul[^>]*>", "\n", content)
    content = re.sub(r"</ul>", "\n", content)
    content = re.sub(r"<ol[^>]*>", "\n", content)
    content = re.sub(r"</ol>", "\n", content)
    content = re.sub(r"<table[^>]*>", "\n", content)
    content = re.sub(r"</table>", "\n", content)
    content = re.sub(r"<tr[^>]*>", "", content)
    content = re.sub(r"</tr>", "\n", content)
    content = re.sub(r"<td[^>]*>", "| ", content)
    content = re.sub(r"</td>", " ", content)
    content = re.sub(r"<th[^>]*>", "| **", content)
    content = re.sub(r"</th>", "** ", content)
    content = re.sub(r"<[^>]+>", "", content)
    content = _html.unescape(content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


# ---------------------------------------------------------------------------
# REST client + recursive descendant fetch
# ---------------------------------------------------------------------------

class ConfluenceClient:
    def __init__(self, cookie: str | None = None):
        self.cookie = cookie or _get_cookie()
        self.headers = {"Cookie": self.cookie, "Accept": "application/json"}
        # internal wiki host TLS may not chain to a public root.
        self.client = httpx.Client(base_url=BASE_URL, headers=self.headers, timeout=30, verify=False)

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, endpoint: str, params: dict = None) -> dict:
        try:
            resp = self.client.get(endpoint, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in (401, 403):
                raise ConfluenceAuthError("认证失败或无权限，请确认 wiki 登录态有效且有该页面权限")
            if status == 404:
                raise ConfluenceError("页面不存在")
            raise ConfluenceError(f"请求失败 (HTTP {status})")
        except httpx.TimeoutException:
            raise ConfluenceError("请求超时，请检查网络连接")
        except httpx.RequestError as e:
            raise ConfluenceError(f"网络请求失败: {type(e).__name__}")

    def get_page(self, page_id: str) -> dict:
        """Fetch a single page's full content + metadata."""
        return self._get(
            f"/rest/api/content/{page_id}",
            {"expand": "body.storage,title,space,version,ancestors"},
        )

    def get_child_pages(self, page_id: str) -> list[dict]:
        """Immediate child pages of page_id (paginated)."""
        out: list[dict] = []
        start = 0
        limit = 200
        while True:
            data = self._get(
                f"/rest/api/content/{page_id}/child/page",
                {"limit": limit, "start": start},
            )
            results = data.get("results", [])
            out.extend(results)
            if len(results) < limit:
                break
            start += limit
        return out


def _page_url(page_id: str) -> str:
    return f"{BASE_URL}/pages/viewpage.action?pageId={page_id}"


def scrape_confluence_recursive(url: str, on_progress=None) -> list[dict]:
    """Fetch a Confluence page and ALL its descendant pages.

    Walks the page tree breadth-first via the /child/page endpoint (this
    Confluence instance does not support /descendant/page). Returns a list of
    result dicts matching the Tencent-doc scraper schema:
        {"url", "title", "content", "depth", "children": [child page urls],
         "parent_url", "error"}

    `on_progress` (sync callable) is invoked with each result dict as it is
    produced, so callers can stream progress.
    """
    if not validate_confluence_url(url):
        raise ValueError("不支持的链接，仅支持已配置的 wiki 站点")

    root_id = extract_page_id(url)
    client = ConfluenceClient()
    results: list[dict] = []

    def _emit(result: dict):
        results.append(result)
        if on_progress:
            on_progress(result)

    try:
        # 示例系统 over the page tree. Each queue item carries (page_id, depth, parent_id).
        queue: list[tuple[str, int, str | None]] = [(root_id, 0, None)]
        visited: set[str] = set()

        while queue:
            page_id, depth, parent_id = queue.pop(0)
            if page_id in visited:
                continue
            visited.add(page_id)

            # Fetch this page's content.
            try:
                page = client.get_page(page_id)
                title = page.get("title", "未命名页面")
                body_html = page.get("body", {}).get("storage", {}).get("value", "")
                md = html_to_markdown(body_html, page_id)
                page_error = None
            except Exception as e:
                title, md, page_error = "", "", str(e)

            # Discover children (even if content fetch failed, try the tree).
            children = []
            if page_error is None:
                try:
                    children = client.get_child_pages(page_id)
                except Exception:
                    children = []
            child_ids = [str(c.get("id")) for c in children if str(c.get("id")) not in visited]

            _emit({
                "url": _page_url(page_id),
                "title": title,
                "content": md,
                "depth": depth,
                "children": [_page_url(cid) for cid in child_ids],
                "parent_url": _page_url(parent_id) if parent_id else None,
                "error": page_error,
            })

            for cid in child_ids:
                queue.append((cid, depth + 1, page_id))
    finally:
        client.close()

    return results
