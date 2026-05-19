from pathlib import Path
from docx import Document as DocxDocument
from python_calamine import CalamineWorkbook
from pptx import Presentation


def parse_docx(file_path: str) -> str:
    doc = DocxDocument(file_path)
    lines = []
    for para in doc.paragraphs:
        if para.style.name.startswith("Heading"):
            level = int(para.style.name[-1]) if para.style.name[-1].isdigit() else 1
            lines.append(f"{'#' * level} {para.text}")
        elif para.text.strip():
            lines.append(para.text)
    for table in doc.tables:
        lines.append("")
        headers = [cell.text.strip() for cell in table.rows[0].cells]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in table.rows[1:]:
            cells = [cell.text.strip() for cell in row.cells]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def parse_xlsx(file_path: str) -> str:
    wb = CalamineWorkbook.from_path(file_path)
    lines = []
    for sheet_name in wb.sheet_names:
        data = wb.get_sheet_by_name(sheet_name).to_python()
        if not data:
            continue
        lines.append(f"## {sheet_name}")
        lines.append("")

        max_col = max(len(row) for row in data)
        normalized = []
        for row in data:
            cells = []
            for val in row:
                if val is None:
                    cells.append("")
                else:
                    s = str(val).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
                    cells.append(s)
            while len(cells) < max_col:
                cells.append("")
            normalized.append(cells)

        groups = _split_column_groups(normalized)
        if groups:
            for group in groups:
                lines.extend(_render_table(group))
                lines.append("")
        else:
            lines.extend(_render_table(normalized))
            lines.append("")
    return "\n".join(lines)


def _split_column_groups(rows: list[list[str]]) -> list[list[list[str]]] | None:
    """Split a wide table into groups separated by empty columns."""
    if not rows or len(rows[0]) <= 6:
        return None

    col_count = len(rows[0])
    empty_cols = []
    for col_idx in range(col_count):
        if all(row[col_idx].strip() == "" for row in rows):
            empty_cols.append(col_idx)

    if not empty_cols:
        return None

    boundaries = []
    start = 0
    for ec in empty_cols:
        if ec > start:
            boundaries.append((start, ec))
        start = ec + 1
    if start < col_count:
        boundaries.append((start, col_count))

    if len(boundaries) <= 1:
        return None

    groups = []
    for col_start, col_end in boundaries:
        group = []
        for row in rows:
            group_row = row[col_start:col_end]
            if any(cell.strip() for cell in group_row):
                group.append(group_row)
        if group:
            groups.append(group)
    return groups


def _render_table(rows: list[list[str]]) -> list[str]:
    """Render rows as a markdown table."""
    if not rows:
        return []
    col_count = max(len(row) for row in rows)
    lines = []
    headers = rows[0]
    while len(headers) < col_count:
        headers.append("")
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    for row in rows[1:]:
        while len(row) < col_count:
            row.append("")
        lines.append("| " + " | ".join(row) + " |")
    return lines


def parse_xls(file_path: str) -> str:
    return parse_xlsx(file_path)


def parse_pptx(file_path: str) -> str:
    prs = Presentation(file_path)
    lines = []
    for i, slide in enumerate(prs.slides, 1):
        lines.append(f"## Slide {i}")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def parse_text(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8")


PARSERS = {
    ".docx": parse_docx,
    ".xlsx": parse_xlsx,
    ".xls": parse_xls,
    ".pptx": parse_pptx,
    ".txt": parse_text,
    ".md": parse_text,
}


def parse_file(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    parser = PARSERS.get(ext)
    if not parser:
        raise ValueError(f"不支持的文件格式: {ext}")
    return parser(file_path)
