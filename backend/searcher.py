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
    is_original_keyword: bool = False


def _get_kb_dir() -> str:
    return load_settings().knowledge_base_dir


def _expand_keywords(keywords: list[str]) -> list[str]:
    """Expand compound keywords into shorter sub-terms for better matching.

    Strategy:
    - Keep original keyword
    - For 4-char Chinese compounds: split into two 2-char words
    - For 3+ char Chinese phrases: add variant without last char (e.g. 覆盖率→覆盖)
    - For English/numbers: keep as-is, skip pure numbers under 3 chars
    """
    import re
    expanded = []
    seen = set()
    for kw in keywords:
        kw = kw.strip()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        expanded.append(kw)
        if len(kw) <= 2:
            continue
        parts = re.findall(r'[a-zA-Z0-9]+|[一-鿿]+', kw)
        for part in parts:
            if len(part) <= 1:
                continue
            if part[0] >= '一':
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
                    continue
                if part not in seen:
                    seen.add(part)
                    expanded.append(part)
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


def grep_search(keywords: list[str], file_pattern: str = "*", context_lines: int = 3) -> list[SearchResult]:
    results = []
    kb_dir = _get_kb_dir()
    kb_path = Path(kb_dir)
    if not kb_path.exists():
        return results

    expanded = _expand_keywords(keywords)
    priority_keywords = set()
    for kw in keywords:
        if len(kw) >= 3 and any('一' <= c <= '鿿' for c in kw):
            priority_keywords.add(kw)
            if len(kw) >= 4:
                priority_keywords.add(kw[:-1])

    for keyword in expanded:
        parsed = _rg_search(keyword, str(kb_path), file_pattern)
        is_priority = keyword in priority_keywords
        for r in parsed:
            r.is_original_keyword = is_priority
        results.extend(parsed)

    seen = set()
    unique = []
    for r in results:
        key = (r.file, r.line_number)
        if key not in seen:
            seen.add(key)
            unique.append(r)
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


def read_file_content(file_path: str, start_line: int = 0, end_line: int = -1) -> str:
    full_path = Path(_get_kb_dir()) / file_path
    if not full_path.exists():
        return ""
    lines = full_path.read_text(encoding="utf-8").splitlines()
    if end_line == -1:
        end_line = len(lines)
    return "\n".join(lines[start_line:end_line])
