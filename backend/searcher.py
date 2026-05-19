import subprocess
import os
from pathlib import Path
from dataclasses import dataclass
from config import load_settings


@dataclass
class SearchResult:
    file: str
    line_number: int
    content: str
    context_before: list[str]
    context_after: list[str]


def _get_kb_dir() -> str:
    return load_settings().knowledge_base_dir


def _expand_keywords(keywords: list[str]) -> list[str]:
    """Expand compound keywords into shorter sub-terms for better grep matching.

    LLM tends to generate compound terms like '直播值班' or '部门统计' that don't
    appear as-is in documents. We keep the original and also add shorter segments.
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
            if part[0] >= '一' and len(part) >= 2:
                for i in range(len(part) - 1):
                    bigram = part[i:i+2]
                    if bigram not in seen:
                        seen.add(bigram)
                        expanded.append(bigram)
            elif part not in seen:
                seen.add(part)
                expanded.append(part)
    return expanded


def grep_search(keywords: list[str], file_pattern: str = "*", context_lines: int = 3) -> list[SearchResult]:
    results = []
    kb_dir = _get_kb_dir()
    kb_path = Path(kb_dir)
    if not kb_path.exists():
        return results

    expanded = _expand_keywords(keywords)

    for keyword in expanded:
        try:
            cmd = [
                "grep", "-rni",
                f"--include={file_pattern}",
                f"-C{context_lines}",
                keyword,
                str(kb_path),
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, encoding="utf-8"
            )
            if proc.returncode == 0:
                results.extend(_parse_grep_output(proc.stdout, kb_dir))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            results.extend(_fallback_search(keyword, kb_path, file_pattern, context_lines))

    seen = set()
    unique = []
    for r in results:
        key = (r.file, r.line_number)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _parse_grep_output(output: str, kb_dir: str) -> list[SearchResult]:
    results = []
    blocks = output.strip().split("--\n")
    for block in blocks:
        lines = block.strip().split("\n")
        for line in lines:
            if not line:
                continue
            file_path, line_num, content = _split_grep_line(line)
            if file_path is None:
                continue
            rel_path = os.path.relpath(file_path, kb_dir)
            results.append(SearchResult(
                file=rel_path,
                line_number=line_num,
                content=content.strip(),
                context_before=[],
                context_after=[],
            ))
    return results


def _split_grep_line(line: str):
    """Parse a grep output line, handling Windows drive letters (e.g. C:\\path:10:content)."""
    # Skip the drive letter prefix if present (e.g. "C:")
    offset = 0
    if len(line) >= 2 and line[1] == ':' and line[0].isalpha():
        offset = 2

    # Find file_path:line_number:content after the drive prefix
    rest = line[offset:]
    parts = rest.split(":", 2)
    if len(parts) < 3:
        return None, None, None
    file_path = line[:offset] + parts[0]
    try:
        line_num = int(parts[1])
    except ValueError:
        return None, None, None
    return file_path, line_num, parts[2]


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
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                results.append(SearchResult(
                    file=str(file_path.relative_to(kb_path)),
                    line_number=i + 1,
                    content=line.strip(),
                    context_before=[l.strip() for l in lines[start:i]],
                    context_after=[l.strip() for l in lines[i + 1:end]],
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
