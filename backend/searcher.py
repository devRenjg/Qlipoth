import subprocess
import os
import json as json_mod
from pathlib import Path
from dataclasses import dataclass
from config import load_settings

RG_PATH = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "rg.exe")
if not os.path.exists(RG_PATH):
    RG_PATH = "rg"


@dataclass
class SearchResult:
    file: str
    line_number: int
    content: str
    context_before: list[str]
    context_after: list[str]
    matched_keyword: str = ""  # 命中该行的(扩展后)关键词,用于 IDF 覆盖度打分


def _get_kb_dir() -> str:
    return load_settings().knowledge_base_dir


def _expand_keywords(keywords: list[str]) -> list[str]:
    """Expand compound keywords into shorter sub-terms for better matching.

    Strategy:
    - Keep original keyword
    - For 4-char Chinese compounds: split into two 2-char words
    - For 3+ char Chinese phrases: add variant without last char (e.g. 覆盖率→覆盖)
    - Number+unit prefix: 数字后跟"年/月/届"等单位时,剥离单位前缀取核心实体
      (e.g. 26年春晚→春晚,否则核心词被时间前缀粘连导致漏召)
    - For English/numbers: keep as-is, skip pure numbers under 3 chars
    """
    import re
    _UNIT_PREFIX = set("年月日届期季版号周次轮")
    expanded = []
    seen = set()
    for kw in keywords:
        # 健壮性：LLM 偶尔把关键词返回成 dict/list，统一强制为字符串，避免 .strip() 崩溃
        if not isinstance(kw, str):
            if isinstance(kw, dict):
                kw = str(kw.get("keyword") or kw.get("word") or "")
            elif isinstance(kw, (list, tuple)):
                kw = " ".join(str(x) for x in kw)
            else:
                kw = str(kw)
        kw = kw.strip()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        expanded.append(kw)
        if len(kw) <= 2:
            continue
        parts = re.findall(r'[a-zA-Z0-9]+|[一-鿿]+', kw)
        prev_is_digit = False
        for part in parts:
            is_digit_part = part[0] < '一' and part.isdigit()
            if len(part) <= 1:
                prev_is_digit = is_digit_part
                continue
            if part[0] >= '一':
                # 数字+单位前缀:剥离开头的时间/序数单位字,取核心实体
                if prev_is_digit:
                    core = part
                    while len(core) >= 3 and core[0] in _UNIT_PREFIX:
                        core = core[1:]
                    if 2 <= len(core) < len(part) and core not in seen:
                        seen.add(core)
                        expanded.append(core)
                if len(part) >= 3:
                    variant = part[:-1]
                    if variant not in seen:
                        seen.add(variant)
                        expanded.append(variant)
                if len(part) == 4:
                    half1 = part[:2]
                    half2 = part[2:]
                    if half1 not in seen:
                        seen.add(half1)
                        expanded.append(half1)
                    if half2 not in seen:
                        seen.add(half2)
                        expanded.append(half2)
            else:
                if part.isdigit() and len(part) <= 2:
                    prev_is_digit = is_digit_part
                    continue
                if part not in seen:
                    seen.add(part)
                    expanded.append(part)
            prev_is_digit = is_digit_part
    return expanded


def _rg_search(keyword: str, kb_path: str, file_pattern: str = "*") -> list[SearchResult]:
    """Run ripgrep for a single keyword, return structured results."""
    cmd = [
        RG_PATH,
        "--json",
        "--ignore-case",
        "--max-count", "200",
    ]
    if file_pattern != "*":
        cmd.extend(["--glob", file_pattern])

    cmd.extend([keyword, kb_path])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, encoding="utf-8"
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return _fallback_search(keyword, Path(kb_path), file_pattern, 0)

    if proc.returncode not in (0, 1):
        return _fallback_search(keyword, Path(kb_path), file_pattern, 0)

    results = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        try:
            msg = json_mod.loads(line)
        except json_mod.JSONDecodeError:
            continue
        if msg.get("type") != "match":
            continue
        data = msg["data"]
        file_path = data["path"]["text"]
        rel_path = os.path.relpath(file_path, kb_path)
        line_number = data["line_number"]
        content = data["lines"]["text"].strip()
        results.append(SearchResult(
            file=rel_path,
            line_number=line_number,
            content=content,
            context_before=[],
            context_after=[],
        ))
    return results


class SearchResults(list):
    """list 子类,额外携带排序所需的元数据,不破坏现有迭代/切片用法。"""
    original_keywords: list[str]
    keyword_df: dict[str, int]  # 每个(扩展)关键词命中的文件数,用于 IDF


def grep_search(keywords: list[str], file_pattern: str = "*", context_lines: int = 3) -> "SearchResults":
    # 健壮性：把关键词统一强制为非空字符串（LLM 偶尔返回 dict/list/嵌套）
    def _as_str(k):
        if isinstance(k, str):
            return k
        if isinstance(k, dict):
            return str(k.get("keyword") or k.get("word") or "")
        if isinstance(k, (list, tuple)):
            return " ".join(str(x) for x in k)
        return str(k)
    keywords = [s for s in (_as_str(k).strip() for k in (keywords or [])) if s]
    results = []
    kb_dir = _get_kb_dir()
    kb_path = Path(kb_dir)
    if not kb_path.exists():
        out = SearchResults()
        out.original_keywords = list(keywords)
        out.keyword_df = {}
        return out

    expanded = _expand_keywords(keywords)

    keyword_df: dict[str, int] = {}
    for keyword in expanded:
        parsed = _rg_search(keyword, str(kb_path), file_pattern)
        for r in parsed:
            r.matched_keyword = keyword
        keyword_df[keyword] = len({r.file for r in parsed})
        results.extend(parsed)

    seen = set()
    unique = SearchResults()
    for r in results:
        key = (r.file, r.line_number)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    unique.original_keywords = [k.strip() for k in keywords if k.strip()]
    unique.keyword_df = keyword_df
    return unique


def _fallback_search(keyword: str, kb_path: Path, file_pattern: str, context_lines: int) -> list[SearchResult]:
    results = []
    import fnmatch
    for file_path in kb_path.rglob("*"):
        if not file_path.is_file():
            continue
        if file_pattern != "*" and not fnmatch.fnmatch(file_path.name, file_pattern):
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, PermissionError):
            continue
        keyword_lower = keyword.lower()
        for i, line in enumerate(lines):
            if keyword_lower in line.lower():
                results.append(SearchResult(
                    file=str(file_path.relative_to(kb_path)),
                    line_number=i + 1,
                    content=line.strip(),
                    context_before=[],
                    context_after=[],
                ))
    return results


def _safe_kb_path(file_path: str) -> "Path | None":
    """把 file_path 限定在知识库目录内，防路径穿越。越界/绝对路径/非.md 返回 None。"""
    if not file_path or file_path.startswith(("/", "\\")) or ".." in file_path.replace("\\", "/").split("/"):
        return None
    kb_root = Path(_get_kb_dir()).resolve()
    try:
        target = (kb_root / file_path).resolve()
    except Exception:
        return None
    if not target.is_relative_to(kb_root):
        return None
    if target.suffix.lower() != ".md":
        return None
    return target


def read_file_content(file_path: str, start_line: int = 0, end_line: int = -1) -> str:
    full_path = _safe_kb_path(file_path)
    if not full_path or not full_path.exists():
        return ""
    lines = full_path.read_text(encoding="utf-8").splitlines()
    if end_line == -1:
        end_line = len(lines)
    return "\n".join(lines[start_line:end_line])
