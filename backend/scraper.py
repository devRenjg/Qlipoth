import re
import base64
import zlib
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


ALLOWED_HOSTS = {
    "docs.qq.com", "sheet.qq.com", "slide.qq.com",
    "doc.weixin.qq.com", "sheet.weixin.qq.com",
}

BROWSER_DATA_DIR = Path(__file__).parent / ".browser_data"
CHROME_PATH = "C:/Program Files/Google/Chrome/Application/chrome.exe"


def validate_tencent_doc_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and parsed.hostname in ALLOWED_HOSTS
    except Exception:
        return False


async def scrape_tencent_doc(url: str, timeout_ms: int = 60000) -> tuple[str, str]:
    """Scrape a Tencent/WeChat Doc URL by intercepting the opendoc API response."""
    if not validate_tencent_doc_url(url):
        raise ValueError(f"不支持的链接，仅支持: {', '.join(ALLOWED_HOSTS)}")

    BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA_DIR),
            headless=False,
            executable_path=CHROME_PATH,
            viewport={"width": 1920, "height": 1080},
            args=["--disable-blink-features=AutomationControlled"],
        )

        try:
            page = await context.new_page()

            captured = {}

            async def on_response(response):
                resp_url = response.url
                if "dop-api/get/sheet" in resp_url:
                    try:
                        captured["sheet"] = await response.json()
                    except Exception:
                        pass
                elif "dop-api/opendoc" in resp_url:
                    try:
                        captured["doc"] = await response.json()
                    except Exception:
                        pass

            page.on("response", on_response)

            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            await page.wait_for_timeout(8000)

            data = captured.get("sheet") or captured.get("doc")

            if data is None:
                body_text = await page.inner_text("body")
                if any(kw in body_text for kw in ("登录", "扫码", "验证身份")):
                    raise PermissionError(
                        "需要登录。首次使用时会弹出浏览器窗口，请在窗口中完成登录后重试。"
                    )
                raise RuntimeError("未能拦截到文档数据接口，请确认链接可正常访问")

            title = _extract_title(data)
            if not title or title == "未命名文档":
                page_title = await page.title()
                page_title = re.sub(r"\s*[-|–—]\s*(腾讯文档|企业微信).*$", "", page_title).strip()
                if page_title:
                    title = page_title

            text = _extract_text_from_response(data)

            if not text or len(text) < 10:
                raise RuntimeError("文档内容为空或提取失败")

            markdown = f"# {title}\n\n> 来源: {url}\n\n{text}"
            return title, markdown

        except PlaywrightTimeout:
            raise TimeoutError(f"页面加载超时（{timeout_ms // 1000}s），请检查链接是否可访问")
        finally:
            await context.close()


def _extract_title(data: dict) -> str:
    cv = data.get("clientVars", {})
    title = cv.get("padTitle", "") or cv.get("title", "")
    if not title:
        body_data = data.get("bodyData", {})
        title = body_data.get("pageTitle", "")
    if not title:
        # Sheet format: title might be in the HTML page data
        html_data = data.get("htmlData", {})
        title = html_data.get("title", "")
    return title.strip() or "未命名文档"


def _extract_text_from_response(data: dict) -> str:
    """Extract plain text from the opendoc/sheet API response."""
    # Try doc format: clientVars.collab_client_vars.initialAttributedText
    try:
        cv = data.get("clientVars", {})
        ccv = cv.get("collab_client_vars", {})
        iat = ccv.get("initialAttributedText", {})
        text_list = iat.get("text", [])

        if text_list and isinstance(text_list[0], str):
            raw = base64.b64decode(text_list[0])
            result = _decode_protobuf_text(raw)
            if result and len(result) > 10:
                return result
    except Exception:
        pass

    # Try sheet format: data.initialAttributedText.text[0].related_sheet
    try:
        d = data.get("data", data)
        iat = d.get("initialAttributedText", {})
        text_list = iat.get("text", [])

        if text_list and isinstance(text_list[0], dict):
            related_sheet = text_list[0].get("related_sheet", "")
            if related_sheet:
                raw = zlib.decompress(base64.b64decode(related_sheet))
                return _decode_protobuf_text(raw)
    except Exception:
        pass

    return ""


def _decode_protobuf_text(raw: bytes) -> str:
    """Extract UTF-8 text segments from protobuf binary data."""
    segments = []
    i = 0
    current = bytearray()

    while i < len(raw):
        byte = raw[i]
        if byte == 0x0D:  # CR = paragraph separator in this format
            if current:
                try:
                    segments.append(current.decode("utf-8"))
                except UnicodeDecodeError:
                    pass
                current = bytearray()
            segments.append("\n")
            i += 1
        elif 0x20 <= byte <= 0x7E:  # ASCII printable
            current.append(byte)
            i += 1
        elif 0xC0 <= byte <= 0xFD:  # UTF-8 multi-byte start
            if byte < 0xE0:
                char_len = 2
            elif byte < 0xF0:
                char_len = 3
            elif byte < 0xF8:
                char_len = 4
            else:
                if current:
                    try:
                        segments.append(current.decode("utf-8"))
                    except UnicodeDecodeError:
                        pass
                    current = bytearray()
                i += 1
                continue
            if i + char_len <= len(raw):
                char_bytes = raw[i : i + char_len]
                try:
                    char_bytes.decode("utf-8")
                    current.extend(char_bytes)
                    i += char_len
                except UnicodeDecodeError:
                    if current:
                        try:
                            segments.append(current.decode("utf-8"))
                        except UnicodeDecodeError:
                            pass
                        current = bytearray()
                    i += 1
            else:
                if current:
                    try:
                        segments.append(current.decode("utf-8"))
                    except UnicodeDecodeError:
                        pass
                    current = bytearray()
                i += 1
        else:
            if current:
                try:
                    segments.append(current.decode("utf-8"))
                except UnicodeDecodeError:
                    pass
                current = bytearray()
            i += 1

    if current:
        try:
            segments.append(current.decode("utf-8"))
        except UnicodeDecodeError:
            pass

    full_text = "".join(segments)
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)
    return full_text.strip()


def _extract_embedded_links(text: str) -> list[str]:
    """Extract embedded document URLs from scraped text."""
    pattern = r"HYPERLINK\s+(https://(?:doc|sheet|slide)\.weixin\.qq\.com/[^\s]+)"
    raw_urls = re.findall(pattern, text)

    cleaned = []
    seen = set()
    for url in raw_urls:
        url = re.split(r"\s+docLink", url)[0]
        url = url.split("&isEnterEdit")[0]
        parsed = urlparse(url)
        if parsed.hostname not in ALLOWED_HOSTS:
            continue
        normalized = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(url)

    return cleaned


async def _scrape_single_in_context(context, url: str, timeout_ms: int = 60000) -> dict:
    """Scrape a single doc using an existing browser context. Returns result dict."""
    page = await context.new_page()
    captured = {}

    async def on_response(response):
        resp_url = response.url
        if "dop-api/get/sheet" in resp_url:
            try:
                captured["sheet"] = await response.json()
            except Exception:
                pass
        elif "dop-api/opendoc" in resp_url:
            try:
                captured["doc"] = await response.json()
            except Exception:
                pass

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        await page.wait_for_timeout(8000)

        # Prefer sheet data for sheet URLs, doc data for doc URLs
        data = captured.get("sheet") or captured.get("doc")

        if data is None:
            body_text = await page.inner_text("body")
            if any(kw in body_text for kw in ("登录", "扫码", "验证身份")):
                return {"url": url, "error": "需要登录", "title": "", "content": ""}
            return {"url": url, "error": "未能获取文档数据", "title": "", "content": ""}

        title = _extract_title(data)
        if not title or title == "未命名文档":
            page_title = await page.title()
            page_title = re.sub(r"\s*[-|–—]\s*(腾讯文档|企业微信).*$", "", page_title).strip()
            if page_title:
                title = page_title

        text = _extract_text_from_response(data)

        if not text or len(text) < 10:
            return {"url": url, "error": "文档内容为空", "title": title, "content": ""}

        return {"url": url, "title": title, "content": text, "error": None}

    except PlaywrightTimeout:
        return {"url": url, "error": "页面加载超时", "title": "", "content": ""}
    except Exception as e:
        return {"url": url, "error": str(e), "title": "", "content": ""}
    finally:
        await page.close()


async def scrape_tencent_doc_recursive(
    url: str, max_depth: int = 5, timeout_ms: int = 60000
) -> list[dict]:
    """
    Recursively scrape a document and its embedded sub-documents.
    Returns list of:
        {"url", "title", "content", "depth", "children": [urls], "parent_url", "error"}
    """
    if not validate_tencent_doc_url(url):
        raise ValueError(f"不支持的链接，仅支持: {', '.join(ALLOWED_HOSTS)}")

    BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    visited = set()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA_DIR),
            headless=False,
            executable_path=CHROME_PATH,
            viewport={"width": 1920, "height": 1080},
            args=["--disable-blink-features=AutomationControlled"],
        )

        try:
            queue = [(url, 0, None)]  # (url, depth, parent_url)

            while queue:
                current_url, depth, parent_url = queue.pop(0)

                normalized = urlparse(current_url)
                norm_key = f"{normalized.hostname}{normalized.path}"
                if norm_key in visited:
                    continue
                visited.add(norm_key)

                result = await _scrape_single_in_context(context, current_url, timeout_ms)
                result["depth"] = depth
                result["parent_url"] = parent_url
                result["children"] = []

                if result["error"] is None and result["content"]:
                    child_links = _extract_embedded_links(result["content"])
                    child_links_filtered = []
                    for link in child_links:
                        link_parsed = urlparse(link)
                        link_key = f"{link_parsed.hostname}{link_parsed.path}"
                        if link_key not in visited:
                            child_links_filtered.append(link)
                    result["children"] = child_links_filtered

                    if depth < max_depth:
                        for child_url in child_links_filtered:
                            queue.append((child_url, depth + 1, current_url))

                results.append(result)

        finally:
            await context.close()

    return results
