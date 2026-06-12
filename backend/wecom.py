"""企业微信 CLI (wecom-cli) 封装。

通过 subprocess 调用全局安装的 wecom-cli，提供"创建在线文档 + 写入 Markdown 内容"
的能力，供保障清单导出使用。

凭证由 `wecom-cli init` 一次性配置（加密存于 ~/.config/wecom/bot.enc），本模块不碰凭证。

wecom-cli 返回的是 MCP 包裹格式：
    {"jsonrpc":"2.0","result":{"content":[{"text":"<内层JSON字符串>","type":"text"}],"isError":false}}
内层 JSON 才是业务结果（errcode/errmsg/url/docid）。本模块负责拆这两层。
"""
import os
import json
import shutil
import asyncio
import subprocess
from concurrent.futures import ThreadPoolExecutor

from config import load_settings

_executor = ThreadPoolExecutor(max_workers=2)
_CALL_TIMEOUT = 90  # 秒；大文档写入可能较慢，给足余量


class WecomError(Exception):
    """企微 CLI 调用失败（凭证失效/权限不足/网络/业务 errcode 非 0）。"""
    def __init__(self, msg: str, errcode=None):
        super().__init__(msg)
        self.errcode = errcode


# 限流相关 errcode（读文档频率超限），需退避重试
RATE_LIMIT_CODES = {851010, 851000}


def _cli_cmd() -> list[str]:
    """构造调用 wecom-cli 的命令前缀。

    优先 `node <wecom.js>` 直调，绕开 Windows 上的 `.cmd` 批处理包装器——后者用
    `%*` 重展开参数，会把 Markdown 内容里的特殊字符（/ \\ & | ( ) 引号 换行）交给
    cmd.exe 解释，导致"命令行太长"/"系统找不到指定的路径"等错误。直调 node 无此问题。
    """
    # 1) 配置里若直接给了 wecom.js 或可执行文件
    configured = getattr(load_settings(), "wecom_cli_path", "") or ""
    if configured and os.path.exists(configured):
        if configured.endswith(".js"):
            return ["node", configured]
        if configured.endswith((".cmd", ".exe", ".ps1")):
            # 尝试推导同目录的 js 入口
            pass
        else:
            return [configured]
    # 2) Windows npm 全局：定位 node_modules/@wecom/cli/bin/wecom.js
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        js = os.path.join(appdata, "npm", "node_modules", "@wecom", "cli", "bin", "wecom.js")
        if os.path.exists(js):
            return ["node", js]
    # 3) PATH 上的 wecom-cli（非 Windows，或上面都没命中）
    found = shutil.which("wecom-cli")
    if found:
        return [found]
    raise WecomError("未找到 wecom-cli，请先 npm install -g @wecom/cli 并运行 wecom-cli init")


def _unwrap(stdout: str) -> dict:
    """拆 MCP 双层 JSON，返回内层业务结果 dict。"""
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError:
        raise WecomError(f"wecom-cli 返回非 JSON：{stdout[:200]}")
    if "error" in outer and outer.get("error"):
        raise WecomError(f"wecom-cli 错误：{outer['error']}")
    result = outer.get("result", {})
    contents = result.get("content", [])
    if not contents:
        # 某些只读失败会直接把错误放在外层文本
        raise WecomError(f"wecom-cli 无内容返回：{stdout[:200]}")
    text = contents[0].get("text", "")
    try:
        inner = json.loads(text)
    except json.JSONDecodeError:
        # 个别命令可能直接返回纯文本
        return {"_raw": text}
    return inner


def _run_sync(category: str, method: str, args: dict) -> dict:
    cmd_prefix = _cli_cmd()
    payload = json.dumps(args, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [*cmd_prefix, category, method, "--json", payload],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_CALL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise WecomError(f"wecom-cli {category} {method} 调用超时（>{_CALL_TIMEOUT}s）")
    except FileNotFoundError:
        raise WecomError("wecom-cli 未安装或路径错误")
    if proc.returncode != 0 and not proc.stdout:
        raise WecomError(f"wecom-cli 退出码 {proc.returncode}：{(proc.stderr or '')[:200]}")
    # 某些命令（如 edit_doc_content）成功时 stdout 可能为空，returncode=0 即视为成功
    if not (proc.stdout or "").strip():
        if proc.returncode == 0:
            return {}
        raise WecomError(f"wecom-cli 无输出，退出码 {proc.returncode}：{(proc.stderr or '')[:200]}")
    inner = _unwrap(proc.stdout)
    errcode = inner.get("errcode")
    if errcode not in (0, None):
        raise WecomError(f"企微返回错误 errcode={errcode}：{inner.get('errmsg', '')}", errcode=errcode)
    return inner


async def _run(category: str, method: str, args: dict) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run_sync, category, method, args)


async def create_doc(doc_name: str, doc_type: int = 3) -> tuple[str, str]:
    """新建文档（doc_type=3 普通文档）。返回 (docid, url)。"""
    res = await _run("doc", "create_doc", {"doc_type": doc_type, "doc_name": doc_name[:255]})
    docid = res.get("docid", "")
    url = res.get("url", "")
    if not docid or not url:
        raise WecomError(f"创建文档未返回 docid/url：{res}")
    return docid, url


async def edit_doc_content(docid: str, markdown: str) -> None:
    """用 Markdown 覆写文档内容（content_type=1）。"""
    await _run("doc", "edit_doc_content", {"docid": docid, "content": markdown, "content_type": 1})


async def create_doc_with_content(doc_name: str, markdown: str) -> dict:
    """建文档 + 写入内容，返回 {docid, url}。"""
    docid, url = await create_doc(doc_name)
    await edit_doc_content(docid, markdown)
    return {"docid": docid, "url": url}


# ---------------------------------------------------------------------------
# 文档读取（异步轮询）+ base64 图片落地
# ---------------------------------------------------------------------------
import re
import base64
import hashlib

# 图片落地目录：backend/kb_images
KB_IMAGES_DIR = os.path.join(os.path.dirname(__file__), "kb_images")
_DATA_IMG_RE = re.compile(r'!\[([^\]]*)\]\(data:image/(\w+);base64,([^)]+)\)')
_EXT_MAP = {"jpeg": "jpg", "svg+xml": "svg"}


async def get_doc_content(url: str = "", docid: str = "", max_poll: int = 30, interval: float = 2.0) -> str:
    """读取企微文档/表格完整内容，返回 Markdown。异步轮询直到 task_done。

    url 与 docid 二选一。读取失败/超时/空内容会抛 WecomError。
    """
    if not url and not docid:
        raise WecomError("get_doc_content 需要 url 或 docid")
    task_id = None
    for _ in range(max_poll):
        args = {"type": 2}
        if url:
            args["url"] = url
        if docid:
            args["docid"] = docid
        if task_id:
            args["task_id"] = task_id
        res = await _run("doc", "get_doc_content", args)
        if res.get("task_done"):
            content = res.get("content") or ""
            if not content:
                raise WecomError("企微文档内容为空")
            return content
        task_id = res.get("task_id")
        if not task_id:
            # 没有 task_id 又没 done，且有 content 直接返回
            content = res.get("content") or ""
            if content:
                return content
            raise WecomError("企微文档读取未返回 task_id 且无内容")
        await asyncio.sleep(interval)
    raise WecomError(f"企微文档读取轮询超时（{max_poll}次）")


async def get_doc_content_retry(url: str = "", docid: str = "", max_retry: int = 3, base_wait: float = 10.0) -> str:
    """带限流退避重试的读取。遇到 851010/851000 频率限制时退避后重试。

    重试 2 次（共 3 次尝试，退避 10s/20s），既给正常文档被瞬时限流的恢复机会，
    又不在"持续失败的问题文档"上浪费太久（封顶 ~30s）。
    """
    last = None
    for attempt in range(max_retry):
        try:
            return await get_doc_content(url=url, docid=docid)
        except WecomError as e:
            last = e
            if e.errcode in RATE_LIMIT_CODES and attempt < max_retry - 1:
                await asyncio.sleep(base_wait * (attempt + 1))  # 10s, 20s
                continue
            raise
    raise last or WecomError("读取重试耗尽")


def land_base64_images(markdown: str, url_prefix: str = "/api/documents/kb-image/") -> tuple[str, int]:
    """把 markdown 里的 base64 内联图片提取存到 KB_IMAGES_DIR，按内容 hash 命名，
    md 中替换为 `{url_prefix}{hash}.{ext}`。返回 (新markdown, 落地图片数)。
    """
    os.makedirs(KB_IMAGES_DIR, exist_ok=True)
    count = 0

    def _repl(m: "re.Match") -> str:
        nonlocal count
        alt, fmt, b64 = m.group(1), m.group(2).lower(), m.group(3)
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return m.group(0)  # 解码失败保留原样
        ext = _EXT_MAP.get(fmt, fmt)
        h = hashlib.sha1(raw).hexdigest()
        fname = f"{h}.{ext}"
        fpath = os.path.join(KB_IMAGES_DIR, fname)
        if not os.path.exists(fpath):
            with open(fpath, "wb") as f:
                f.write(raw)
        count += 1
        return f"![{alt}]({url_prefix}{fname})"

    new_md = _DATA_IMG_RE.sub(_repl, markdown)
    return new_md, count


def normalize_vertical_tables(markdown: str) -> tuple[str, int]:
    """企微 doc 内嵌表格常被导出成"竖排单格"——每个单元格单独占一行（`|值`，行内无其它 `|`），
    导致行列关系丢失、检索命中不到、LLM 读不懂。

    保守合并：把**连续**的竖排单格行（含被短裸文本行打断的续接）拼成一行
    `| a | b | c |`，让同一逻辑行的数据物理相邻、可被检索命中。

    安全原则：只合并、不臆测列归属（损坏结构无法可靠还原行列，强行对列会制造错位假数据）；
    已是横排多列的行（`|a|b|c|`）、分隔符行、普通正文都原样保留。返回 (新md, 合并的竖排块数)。
    """
    import re as _re
    lines = markdown.splitlines()
    out = []
    buf = []
    merged = 0

    def is_sep(l):
        return bool(_re.match(r'^\s*\|(\s*:?-{3,}:?\s*\|)+\s*$', l))

    def flush():
        nonlocal merged
        if not buf:
            return
        cells = []
        for b in buf:
            c = b.strip().lstrip('|').strip()
            if c:
                cells.append(c)
        if len(cells) >= 2:
            out.append('| ' + ' | '.join(cells) + ' |')
            merged += 1
        else:
            out.extend(buf)  # 只有0-1个有效格，不值得合并，原样还原
        buf.clear()

    pending_text = []  # 暂存竖排块中间的短裸文本（单元格换行溢出）
    for l in lines:
        s = l.strip()
        is_vert = s.startswith('|') and s.count('|') <= 1 and not is_sep(l)
        if is_vert:
            # 短裸文本（<=20字、非空、不以|开头）视为上一格的换行续接，并入缓冲
            if pending_text and buf:
                buf[-1] = buf[-1] + ' ' + ' '.join(pending_text)
            pending_text = []
            buf.append(s)
        elif buf and s and not s.startswith('|') and not is_sep(l) and len(s) <= 20:
            pending_text.append(s)  # 可能是竖排块中间的溢出文本，先暂存
        else:
            if pending_text and buf:
                buf[-1] = buf[-1] + ' ' + ' '.join(pending_text)
            pending_text = []
            flush()
            out.append(l)
    if pending_text and buf:
        buf[-1] = buf[-1] + ' ' + ' '.join(pending_text)
    flush()
    return "\n".join(out), merged


