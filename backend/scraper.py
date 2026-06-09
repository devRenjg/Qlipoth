import re
import json
import base64
import zlib
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


ALLOWED_HOSTS = {
    "docs.qq.com", "sheet.qq.com", "slide.qq.com",
    "doc.weixin.qq.com", "sheet.weixin.qq.com",
}

BROWSER_DATA_DIR = Path(__file__).parent.parent / ".browser_data"
CHROME_PATH = "C:/Program Files/Google/Chrome/Application/chrome.exe"


def validate_tencent_doc_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and parsed.hostname in ALLOWED_HOSTS
    except Exception:
        return False


def _extract_tab_subid(url: str) -> str:
    """从 smartsheet URL 提取 tab 参数（真正的数据表 subId）。无则空串。"""
    try:
        from urllib.parse import parse_qs
        q = parse_qs(urlparse(url).query)
        return (q.get("tab", [""])[0] or "").strip()
    except Exception:
        return ""


def _extract_pad_id(url: str) -> str:
    """从 URL 路径提取 padId，如 /smartsheet/s3_XXX -> s3_XXX。"""
    try:
        m = re.search(r"/(?:smartsheet|sheet|doc)/([A-Za-z0-9_]+)", urlparse(url).path)
        return m.group(1) if m else ""
    except Exception:
        return ""


async def _fetch_sheet_by_subid(page, pad_id: str, sub_id: str) -> dict | None:
    """在页面上下文里主动请求指定 subId 的 sheet 数据(带登录态cookie)。

    解决 smartsheet 内嵌/多表场景：前端默认加载的子表可能为空，
    URL 的 tab 参数才是用户要看的数据表。带重试，应对页面 xsrf/登录态就绪时机。
    """
    if not pad_id or not sub_id:
        return None
    js = """async ([pad, sub]) => {
      const u = `/dop-api/get/sheet?padId=${pad}&subId=${sub}&startrow=0&endrow=2000`;
      try { const r = await fetch(u, {credentials:'include'}); return await r.json(); }
      catch (e) { return null; }
    }"""
    for attempt in range(3):
        try:
            res = await page.evaluate(js, [pad_id, sub_id])
        except Exception:
            res = None
        if res and res.get("retcode") == 0 and (res.get("data", {}).get("maxrow") or 0) > 0:
            return res
        await page.wait_for_timeout(2000)
    return res if isinstance(res, dict) else None


async def _wait_for_content(page, max_wait_ms: int = 15000) -> None:
    """Wait for doc body to finish loading.

    The opendoc API can return before the editor renders ("内容加载中..."); a fixed
    sleep is either too short (slow docs fail) or wasteful. Poll the body text and
    stop early once the loading placeholder disappears and real content appears.
    """
    step = 1000
    waited = 0
    # minimum settle so late XHR (sheet/mind) gets captured
    await page.wait_for_timeout(3000)
    waited += 3000
    while waited < max_wait_ms:
        try:
            body = await page.inner_text("body")
        except Exception:
            body = ""
        if "内容加载中" not in body and len(body) > 80:
            return
        await page.wait_for_timeout(step)
        waited += step


async def scrape_tencent_doc(url: str, timeout_ms: int = 60000) -> tuple[str, str]:
    """Scrape a Tencent/WeChat Doc URL by intercepting the opendoc API response."""
    if not validate_tencent_doc_url(url):
        raise ValueError(f"不支持的链接，仅支持: {', '.join(ALLOWED_HOSTS)}")

    tab_sub = _extract_tab_subid(url)      # 须在 sanitize 前取(sanitize 会丢弃 tab 参数)
    pad_from_url = _extract_pad_id(url)
    url = _sanitize_doc_url(url)
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
                elif "dop-api/mind/data/get" in resp_url:
                    try:
                        captured["mind"] = await response.json()
                    except Exception:
                        pass
                elif "dop-api/opendoc" in resp_url:
                    try:
                        captured["doc"] = await response.json()
                    except Exception:
                        pass

            page.on("response", on_response)

            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            await _wait_for_content(page)

            data = captured.get("sheet") or captured.get("mind") or captured.get("doc")

            # smartsheet：URL 的 tab 才是数据表，主动拉取(前端默认加载的子表常为空)
            if tab_sub:
                pad_id = pad_from_url or (data or {}).get("data", {}).get("globalPadId", "")
                tab_data = await _fetch_sheet_by_subid(page, pad_id, tab_sub)
                if tab_data and tab_data.get("retcode") == 0:
                    td = tab_data.get("data", {})
                    if (td.get("maxrow") or 0) > 0:
                        data = tab_data

            if data is None:
                body_text = await page.inner_text("body")
                if any(kw in body_text for kw in ("登录", "扫码", "验证身份")):
                    raise PermissionError(
                        "需要登录。首次使用时会弹出浏览器窗口，请在窗口中完成登录后重试。"
                    )
                raise RuntimeError("未能拦截到文档数据接口，请确认链接可正常访问")

            title = _extract_title(data)
            if not title or title == "未命名文档":
                if captured.get("doc"):
                    alt = _extract_title(captured["doc"])
                    if alt and alt != "未命名文档":
                        title = alt
            if not title or title == "未命名文档":
                page_title = await page.title()
                page_title = re.sub(r"\s*[-|–—]\s*(腾讯文档|企业微信).*$", "", page_title).strip()
                if page_title:
                    title = page_title

            text = _extract_text_from_response(data)

            if not text or len(text) < 10:
                text = await _extract_from_dom(page)
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
        title = cv.get("initialTitle", "")
    if not title:
        body_data = data.get("bodyData", {})
        title = body_data.get("pageTitle", "")
    if not title:
        # Sheet format: title might be in the HTML page data
        html_data = data.get("htmlData", {})
        title = html_data.get("title", "")
    if not title:
        # Mind format: title sits on the root topic inside fileData
        d = data.get("data", data)
        mcv = d.get("collab_client_vars", {})
        file_data = mcv.get("fileData")
        if isinstance(file_data, str) and file_data:
            try:
                fd = json.loads(file_data)
                content = fd.get("content", [])
                if content:
                    title = content[0].get("rootTopic", {}).get("title", "")
            except (json.JSONDecodeError, TypeError):
                pass
    return title.strip() or "未命名文档"


def _extract_text_from_response(data: dict) -> str:
    """Extract text from the opendoc/sheet API response, preserving links and images."""
    # Mind (思维导图): data.collab_client_vars.fileData holds a JSON topic tree.
    mind_text = _extract_mind_text(data)
    if mind_text:
        return mind_text

    # Smartsheet (智能表格): data.initialAttributedText.text[0].smartsheet holds
    # b64+zlib-compressed JSON of records.
    smart_text = _extract_smartsheet_text(data)
    if smart_text:
        return smart_text

    # Try doc format: clientVars.collab_client_vars.initialAttributedText
    try:
        cv = data.get("clientVars", {})
        ccv = cv.get("collab_client_vars", {})
        iat = ccv.get("initialAttributedText", {})
        text_list = iat.get("text", [])
        attribs = iat.get("attribs", "")
        pool = iat.get("pool", {})

        if text_list and isinstance(text_list[0], str):
            raw = base64.b64decode(text_list[0])
            result = _decode_protobuf_text(raw)
            if result and len(result) > 10:
                result = _convert_hyperlinks(result)
                images = _extract_images_from_data(data)
                if images:
                    result += "\n\n## 文档图片\n\n" + "\n".join(images)
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
                result = _decode_sheet_data(raw)
                if result and len(result) > 10:
                    result = _convert_hyperlinks(result)
                    return result
                # Fallback to protobuf text extraction
                result = _decode_protobuf_text(raw)
                result = _convert_hyperlinks(result)
                images = _extract_images_from_data(data)
                if images:
                    result += "\n\n## 文档图片\n\n" + "\n".join(images)
                return result
    except Exception:
        pass

    return ""


def _extract_mind_text(data: dict) -> str:
    """Extract a mind-map (思维导图) topic tree into indented markdown.

    Structure: data.collab_client_vars.fileData (JSON string) ->
        content[0].rootTopic { title, children.attached[ {title, children...} ] }
    """
    d = data.get("data", data)
    cv = d.get("collab_client_vars", {})
    if d.get("padType") != "mind" and not cv.get("fileData"):
        return ""
    file_data = cv.get("fileData")
    if not isinstance(file_data, str) or not file_data:
        return ""
    try:
        fd = json.loads(file_data)
    except (json.JSONDecodeError, TypeError):
        return ""

    content = fd.get("content", [])
    if not content:
        return ""

    lines: list[str] = []

    def _coerce_title(val) -> str:
        """A mind title is usually a plain str, but can be a rich-text dict
        like {"children": [{"type": "paragraph", "children": [{"text": "..."}]}]}.
        Recursively collect all nested "text" fragments."""
        if isinstance(val, str):
            return val.strip()
        if isinstance(val, dict):
            parts: list[str] = []

            def _collect(node):
                if isinstance(node, dict):
                    t = node.get("text")
                    if isinstance(t, str):
                        parts.append(t)
                    for child in node.get("children", []) or []:
                        _collect(child)
                elif isinstance(node, list):
                    for child in node:
                        _collect(child)

            _collect(val)
            return "".join(parts).strip()
        return ""

    def _walk(topic: dict, depth: int):
        title = _coerce_title(topic.get("title"))
        if title:
            lines.append(("  " * depth) + "- " + title)
        children = topic.get("children", {})
        attached = children.get("attached", []) if isinstance(children, dict) else []
        for child in attached:
            _walk(child, depth + 1)

    for node in content:
        root = node.get("rootTopic")
        if root:
            _walk(root, 0)

    text = "\n".join(lines).strip()
    return _convert_hyperlinks(text) if text else ""


def _ss_cell_value(cv: dict, opt_map: dict) -> str:
    """解析新版 smartsheet 单元格值，覆盖 文本/单选状态/日期/数字公式 等类型。"""
    import datetime as _dt
    if not isinstance(cv, dict):
        return ""
    # 富文本: key '1' = [{'1':'text','2':值}]
    if isinstance(cv.get("1"), list):
        parts = [x.get("2", "") for x in cv["1"] if isinstance(x, dict) and x.get("1") == "text"]
        if any(parts):
            return " ".join(p for p in parts if p)
    # 单选/状态: key '17' = [optId]，经 opt_map 映射为标签
    if isinstance(cv.get("17"), list):
        return ",".join(opt_map.get(o, str(o)) for o in cv["17"])
    # 日期: key '7' = [{'1': 毫秒时间戳}]
    if isinstance(cv.get("7"), list):
        out = []
        for x in cv["7"]:
            try:
                ts = int(x["1"])
                if ts > 1e11:  # 毫秒
                    out.append(_dt.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d"))
            except Exception:
                pass
        if out:
            return ",".join(out)
    # 数字/公式: key '36'.'1' 是 JSON，data[].text
    m36 = cv.get("36")
    if isinstance(m36, dict) and isinstance(m36.get("1"), str):
        try:
            j = json.loads(m36["1"])
            ds = j.get("data", [])
            vals = [str(e.get("text", "")) for e in ds if e.get("text", "") != ""]
            if vals:
                return ",".join(vals)
        except Exception:
            pass
    return ""


def _parse_smartsheet_v2(inner: dict) -> str:
    """新版智能表格解析：列名在字段定义 '30'，行数据在 ss[0][1].c.2.1。输出 markdown 表格。

    inner 为 initialAttributedText.text 解析出的 dict（含 max_row/max_col/smartsheet 等）。
    """
    raw_ss = inner.get("smartsheet")
    if not isinstance(raw_ss, str) or not raw_ss:
        return ""
    try:
        ss = json.loads(raw_ss)
    except json.JSONDecodeError:
        return ""
    if not (isinstance(ss, list) and ss and isinstance(ss[0], list) and len(ss[0]) >= 2):
        return ""
    meta = ss[0][0].get("c", {})
    rowsc = ss[0][1].get("c", {})
    fields = meta.get("3", {}).get("3", {})
    if not fields:
        return ""
    # 字段名 + 选项标签映射
    fname, opt_map = {}, {}
    for fid, fdef in fields.items():
        fname[fid] = fdef.get("30", fid)
        for tv in fdef.values():
            if isinstance(tv, dict) and isinstance(tv.get("3"), list):
                for o in tv["3"]:
                    if isinstance(o, dict) and "1" in o and "2" in o:
                        opt_map[o["1"]] = o["2"]
    cols = list(fname.values())
    rows = rowsc.get("2", {}).get("1", {})
    if not isinstance(rows, dict) or not rows:
        return ""
    out_rows = []
    for _rid, rd in rows.items():
        cells = rd.get("1", {}) if isinstance(rd, dict) else {}
        if not isinstance(cells, dict):
            continue
        row = {fname.get(fid, fid): _ss_cell_value(cv, opt_map) for fid, cv in cells.items()}
        if any(row.values()):
            out_rows.append(row)
    if not out_rows:
        return ""
    # 输出 markdown 表格
    lines = ["| " + " | ".join(cols) + " |",
             "| " + " | ".join("---" for _ in cols) + " |"]
    for r in out_rows:
        lines.append("| " + " | ".join((r.get(c, "") or "").replace("\n", " ").replace("|", "/") for c in cols) + " |")
    return "\n".join(lines)


def _extract_smartsheet_text(data: dict) -> str:
    """Extract smartsheet (智能表格) cell text. 先试新版结构(v2)，再退回老版 blob 逻辑。"""
    d = data.get("data", data)
    iat = d.get("initialAttributedText", {})
    text_list = iat.get("text", [])
    # 新版：text 本身是一个大 JSON 字符串(含 max_row/smartsheet)
    if isinstance(text_list, str) and text_list.strip().startswith("{"):
        try:
            inner = json.loads(text_list)
            v2 = _parse_smartsheet_v2(inner)
            if v2:
                return v2
        except json.JSONDecodeError:
            pass
    # 新版变体：text[0] 是含 smartsheet 的 dict
    if text_list and isinstance(text_list[0], dict) and "smartsheet" in text_list[0]:
        v2 = _parse_smartsheet_v2(text_list[0])
        if v2:
            return v2
    # ---- 老版逻辑回退 ----
    if not text_list or not isinstance(text_list[0], dict):
        return ""
    blob = text_list[0].get("smartsheet")
    if not isinstance(blob, str) or not blob:
        return ""
    try:
        raw = zlib.decompress(base64.b64decode(blob))
        payload = json.loads(raw)
    except Exception:
        return ""

    fragments: list[str] = []

    def _walk(obj):
        if isinstance(obj, dict):
            if obj.get("k1") == "text" and isinstance(obj.get("k2"), str):
                fragments.append(obj["k2"])
                return
            if obj.get("k2") == "text" and isinstance(obj.get("k1"), str):
                fragments.append(obj["k1"])
                return
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for x in obj:
                _walk(x)

    _walk(payload)

    cleaned = [f.strip() for f in fragments if f and f.strip()]
    if not cleaned:
        return ""
    return "\n".join(cleaned)


def _decode_sheet_data(raw: bytes) -> str:
    """Decode sheet binary data into markdown table format.

    Sheet data is structured differently from doc data.
    We extract readable text segments and try to reconstruct table structure.
    """
    # First try to extract all UTF-8 text segments
    segments = []
    i = 0
    current = bytearray()

    while i < len(raw):
        byte = raw[i]
        if byte == 0x0A or byte == 0x0D:  # LF or CR = row separator
            if current:
                try:
                    text = current.decode("utf-8").strip()
                    if text:
                        segments.append(text)
                except UnicodeDecodeError:
                    pass
                current = bytearray()
            segments.append("\n")
            i += 1
        elif byte == 0x09:  # TAB = cell separator
            if current:
                try:
                    text = current.decode("utf-8").strip()
                    if text:
                        segments.append(text)
                except UnicodeDecodeError:
                    pass
                current = bytearray()
            segments.append("\t")
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
                char_bytes = raw[i:i + char_len]
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

    # Reconstruct: join segments, try to form table rows
    raw_text = "".join(segments)

    # If we have tab-separated content, format as markdown table
    lines = raw_text.split("\n")
    table_lines = [l for l in lines if "\t" in l]

    if table_lines and len(table_lines) > 2:
        result_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if "\t" in line:
                cells = [c.strip() for c in line.split("\t")]
                result_lines.append("| " + " | ".join(cells) + " |")
                if len(result_lines) == 1:
                    result_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
            else:
                result_lines.append(line)
        return "\n".join(result_lines)

    # No table structure found, return as plain text
    clean_lines = [l.strip() for l in lines if l.strip()]
    text = "\n".join(clean_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _convert_hyperlinks(text: str) -> str:
    """Convert HYPERLINK markers in text to markdown link format."""
    # Pattern: HYPERLINK url [optional params] followed by link text
    # Common formats:
    #   HYPERLINK https://... linkText
    #   HYPERLINK https://... docLink linkText
    def _replace_hyperlink(m):
        url = m.group(1)
        # Clean URL params
        url = url.split("&isEnterEdit")[0]
        trailing = m.group(2).strip()
        # Remove known suffixes like "docLink"
        trailing = re.sub(r'^docLink\s*', '', trailing)
        link_text = trailing.strip() if trailing.strip() else url
        return f"[{link_text}]({url})"

    # Match HYPERLINK followed by URL and optional trailing text until newline
    result = re.sub(
        r'HYPERLINK\s+(https?://[^\s]+)\s*([^\n]*)',
        _replace_hyperlink,
        text
    )
    return result


def _extract_images_from_data(data: dict) -> list[str]:
    """Extract image URLs from API response data."""
    images = []
    seen = set()

    def _find_images(obj, depth=0):
        if depth > 10:
            return
        if isinstance(obj, dict):
            # Look for image URL patterns in various known fields
            for key in ("url", "src", "image_url", "imageUrl", "pic_url", "picUrl"):
                val = obj.get(key, "")
                if isinstance(val, str) and _is_image_url(val) and val not in seen:
                    seen.add(val)
                    alt = obj.get("alt", "") or obj.get("name", "") or obj.get("title", "") or ""
                    images.append(f"![{alt}]({val})")
            # Check for image type markers
            if obj.get("type") in ("image", "img", "picture"):
                for key in ("url", "src", "value"):
                    val = obj.get(key, "")
                    if isinstance(val, str) and val.startswith("http") and val not in seen:
                        seen.add(val)
                        alt = obj.get("alt", "") or obj.get("name", "") or ""
                        images.append(f"![{alt}]({val})")
            for v in obj.values():
                _find_images(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _find_images(item, depth + 1)

    # Search in known image-containing fields
    cv = data.get("clientVars", {})
    body = data.get("bodyData", {})
    _find_images(cv.get("images", {}))
    _find_images(cv.get("imageList", []))
    _find_images(body.get("images", {}))
    _find_images(body.get("imageList", []))
    # Also search the full response for any image objects we might have missed
    _find_images(data.get("images", {}))
    _find_images(data.get("imageList", []))

    return images


def _is_image_url(url: str) -> bool:
    """Check if a URL looks like an image."""
    if not url.startswith("http"):
        return False
    lower = url.lower()
    if any(ext in lower for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")):
        return True
    if any(host in lower for host in ("image", "img", "pic", "photo", "cdn")):
        return True
    return False


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


def _sanitize_doc_url(url: str) -> str:
    """Trim rich-text pollution that gets glued onto extracted doc URLs.

    Embedded links pulled from doc bodies can carry trailing rich-text encoding
    (e.g. `&$<uuid>:JZ*FF000000[v8.80.0]...`), since the source markers have no
    whitespace to delimit the URL. A valid Tencent/WeChat doc URL is
    `/doc/<id>?scode=<alnum>`; cut everything once a non-URL token appears.
    """
    # Cut at known rich-text / attribute markers that never belong in a URL.
    for marker in ("&$", "&isEnterEdit", " docLink", "docLink", "[v", ":JZ", "JZ*", "(", "（", "【"):
        idx = url.find(marker)
        if idx != -1:
            url = url[:idx]
    # scode value is alphanumeric only; drop anything after the first illegal char.
    m = re.match(r'(https://[^?]+\?scode=[A-Za-z0-9]+)', url)
    if m:
        return m.group(1)
    # No scode param: keep up to the first non-URL-safe character.
    m = re.match(r'(https://[^\s一-鿿\[\](){}（）【】*!]+)', url)
    return m.group(1) if m else url


def _extract_embedded_links(text: str) -> list[str]:
    """Extract embedded document URLs from scraped text."""
    # Match both raw HYPERLINK format and markdown link format
    patterns = [
        r'HYPERLINK\s+(https://(?:doc|sheet|slide)\.weixin\.qq\.com/[^\s]+)',
        r'\[.*?\]\((https://(?:doc|sheet|slide)\.weixin\.qq\.com/[^)]+)\)',
        r'(https://(?:doc|sheet|slide)\.weixin\.qq\.com/\S+)',
    ]
    raw_urls = []
    for pattern in patterns:
        raw_urls.extend(re.findall(pattern, text))

    cleaned = []
    seen = set()
    for url in raw_urls:
        url = _sanitize_doc_url(url)
        parsed = urlparse(url)
        if parsed.hostname not in ALLOWED_HOSTS:
            continue
        if not parsed.path or parsed.path == "/":
            continue
        normalized = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(url)

    return cleaned


async def _extract_from_dom(page) -> str:
    """Extract content directly from page DOM as fallback for sheets/docs."""
    try:
        # For sheets: try to get all visible cell text
        cells = await page.evaluate("""() => {
            // Try spreadsheet cells
            const cells = document.querySelectorAll(
                '.cell-content, .sheet-cell, td[class*="cell"], .luckysheet-cell-text, ' +
                '.waffle-cell, [data-cell], .excel-cell'
            );
            if (cells.length > 0) {
                const rows = {};
                cells.forEach(cell => {
                    const row = cell.closest('tr, [data-row]');
                    const rowIdx = row ? (row.dataset.row || row.rowIndex || 0) : 0;
                    if (!rows[rowIdx]) rows[rowIdx] = [];
                    const text = cell.innerText.trim();
                    if (text) rows[rowIdx].push(text);
                });
                return Object.values(rows).filter(r => r.length > 0).map(r => r.join('\\t'));
            }
            // Fallback: get all text from body
            return null;
        }""")

        if cells and isinstance(cells, list) and len(cells) > 0:
            lines = []
            for row in cells:
                if "\t" in row:
                    cols = row.split("\t")
                    lines.append("| " + " | ".join(cols) + " |")
                    if len(lines) == 1:
                        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
                else:
                    lines.append(row)
            return "\n".join(lines)

        # Last resort: get all visible text
        text = await page.inner_text("body")
        if text and len(text) > 20:
            # Clean up excessive whitespace
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            return "\n".join(lines[:500])
    except Exception:
        pass
    return ""

async def _scrape_single_in_context(context, url: str, timeout_ms: int = 60000) -> dict:
    """Scrape a single doc using an existing browser context. Returns result dict."""
    tab_sub = _extract_tab_subid(url)      # sanitize 前取 tab
    pad_from_url = _extract_pad_id(url)
    url = _sanitize_doc_url(url)
    page = await context.new_page()
    captured = {}

    async def on_response(response):
        resp_url = response.url
        if "dop-api/get/sheet" in resp_url:
            try:
                captured["sheet"] = await response.json()
            except Exception:
                pass
        elif "dop-api/mind/data/get" in resp_url:
            try:
                captured["mind"] = await response.json()
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
        await _wait_for_content(page)

        # Prefer sheet data for sheet URLs, mind for mind URLs, doc data for doc URLs
        data = captured.get("sheet") or captured.get("mind") or captured.get("doc")

        # smartsheet：URL 的 tab 才是数据表，主动拉取(前端默认子表常为空)
        if tab_sub:
            pad_id = pad_from_url or (data or {}).get("data", {}).get("globalPadId", "")
            tab_data = await _fetch_sheet_by_subid(page, pad_id, tab_sub)
            if tab_data and tab_data.get("retcode") == 0 and (tab_data.get("data", {}).get("maxrow") or 0) > 0:
                data = tab_data

        if data is None:
            body_text = await page.inner_text("body")
            if any(kw in body_text for kw in ("登录", "扫码", "验证身份")):
                return {"url": url, "error": "需要登录", "title": "", "content": ""}
            return {"url": url, "error": "未能获取文档数据", "title": "", "content": ""}

        title = _extract_title(data)
        if not title or title == "未命名文档":
            if captured.get("doc"):
                alt = _extract_title(captured["doc"])
                if alt and alt != "未命名文档":
                    title = alt
        if not title or title == "未命名文档":
            page_title = await page.title()
            page_title = re.sub(r"\s*[-|–—]\s*(腾讯文档|企业微信).*$", "", page_title).strip()
            if page_title:
                title = page_title

        text = _extract_text_from_response(data)

        if not text or len(text) < 10:
            # Fallback: try extracting from page DOM (especially useful for sheets)
            text = await _extract_from_dom(page)
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
    url: str, max_depth: int = 5, timeout_ms: int = 60000, on_progress=None
) -> list[dict]:
    """
    Recursively scrape a document and its embedded sub-documents.
    on_progress: async callback(result_dict) called after each document is scraped.
    Returns list of:
        {"url", "title", "content", "depth", "children": [urls], "parent_url", "error"}
    """
    if not validate_tencent_doc_url(url):
        raise ValueError(f"不支持的链接，仅支持: {', '.join(ALLOWED_HOSTS)}")

    url = _sanitize_doc_url(url)
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

                if on_progress:
                    await on_progress(result)

        finally:
            await context.close()

    return results
