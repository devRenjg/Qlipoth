from pathlib import Path
import re
from docx import Document as DocxDocument
from python_calamine import CalamineWorkbook
from pptx import Presentation


def extract_owners(text: str) -> list[str]:
    """Extract @mentioned people from document text as task owners."""
    matches = re.findall(r'@([一-鿿\w]{2,10})', text)
    seen = set()
    owners = []
    for name in matches:
        if name not in seen:
            seen.add(name)
            owners.append(name)
    return owners


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


def parse_pdf(file_path: str) -> str:
    from pypdf import PdfReader
    reader = PdfReader(file_path)
    lines = []
    has_text = False
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text()
        except Exception:
            text = ""
        if text and text.strip():
            lines.append(f"## 第 {i} 页")
            lines.append(text.strip())
            lines.append("")
            has_text = True

    if has_text:
        return "\n".join(lines)

    # Image-based PDF: render each page to an image via PyMuPDF, then OCR.
    # PyMuPDF rendering avoids pypdf's decompression limit on embedded images.
    return _ocr_pdf_pages(file_path)


def _ocr_image_tesseract(img, tesseract_path: str) -> str:
    """OCR a PIL image, slicing tall images into strips.

    Tesseract rejects images above ~100M pixels ("Image too large"), which
    happens with long marketing/poster PDFs. Slice by height and concatenate.
    """
    import subprocess
    import tempfile

    W, H = img.size
    strip_height = 2000
    parts = []
    for top in range(0, H, strip_height):
        crop = img.crop((0, top, W, min(top + strip_height, H)))
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                tmp_path = tmp.name
            crop.save(tmp_path)
            proc = subprocess.run(
                [tesseract_path, tmp_path, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                capture_output=True, text=True, timeout=60, encoding="utf-8"
            )
            text = (proc.stdout or "").strip()
            if text:
                parts.append(text)
        except Exception:
            continue
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)
    return "\n".join(parts)


def _ocr_pdf_pages(file_path: str, max_pages: int = 30) -> str:
    """Render each PDF page to an image with PyMuPDF, then OCR via Tesseract.

    Rendering whole pages avoids pypdf's per-image decompression limit, which
    fails on PDFs containing large embedded images (LimitReachedError).
    """
    import fitz
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None  # allow tall poster-style pages
    tesseract_path = "C:/Program Files/Tesseract-OCR/tesseract.exe"

    results = []
    try:
        doc = fitz.open(file_path)
    except Exception:
        return ""

    try:
        for i in range(min(doc.page_count, max_pages)):
            try:
                page = doc.load_page(i)
                # 2x zoom for sharper text -> better OCR accuracy
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text = _ocr_image_tesseract(img, tesseract_path)
                if text:
                    results.append(f"## 第 {i+1} 页\n\n{text}")
            except Exception:
                continue
    finally:
        doc.close()

    return "\n\n".join(results)


PARSERS = {
    ".docx": parse_docx,
    ".xlsx": parse_xlsx,
    ".xls": parse_xls,
    ".pptx": parse_pptx,
    ".pdf": parse_pdf,
    ".txt": parse_text,
    ".md": parse_text,
}


def parse_file(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    parser = PARSERS.get(ext)
    if not parser:
        raise ValueError(f"不支持的文件格式: {ext}")
    return parser(file_path)
