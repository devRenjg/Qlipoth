import subprocess
import os
from pathlib import Path
from dataclasses import dataclass
from config import load_settings

settings = load_settings()
KB_DIR = settings.knowledge_base_dir


@dataclass
class SearchResult:
    file: str
    line_number: int
    content: str
    context_before: list[str]
    context_after: list[str]


def grep_search(keywords: list[str], file_pattern: str = "*", context_lines: int = 3) -> list[SearchResult]:
    results = []
    kb_path = Path(KB_DIR)
    if not kb_path.exists():
        return results

    for keyword in keywords:
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
                results.extend(_parse_grep_output(proc.stdout, context_lines))
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


def _parse_grep_output(output: str, context_lines: int) -> list[SearchResult]:
    results = []
    blocks = output.strip().split("--\n")
    for block in blocks:
        lines = block.strip().split("\n")
        for line in lines:
            if not line:
                continue
            parts = line.split(":", 2)
            if len(parts) >= 3:
                file_path = parts[0]
                try:
                    line_num = int(parts[1])
                except ValueError:
                    continue
                content = parts[2]
                rel_path = os.path.relpath(file_path, KB_DIR)
                results.append(SearchResult(
                    file=rel_path,
                    line_number=line_num,
                    content=content.strip(),
                    context_before=[],
                    context_after=[],
                ))
    return results


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
    full_path = Path(KB_DIR) / file_path
    if not full_path.exists():
        return ""
    lines = full_path.read_text(encoding="utf-8").splitlines()
    if end_line == -1:
        end_line = len(lines)
    return "\n".join(lines[start_line:end_line])


def list_files() -> list[dict]:
    kb_path = Path(KB_DIR)
    if not kb_path.exists():
        return []
    files = []
    for f in kb_path.rglob("*"):
        if f.is_file():
            files.append({
                "path": str(f.relative_to(kb_path)),
                "size": f.stat().st_size,
                "name": f.name,
            })
    return files
