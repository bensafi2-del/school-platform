from __future__ import annotations

import io
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pdfplumber
import pytesseract
from PIL import Image, ImageOps
from docx import Document
from openpyxl import load_workbook
import xlrd

from .parsers import classify_document, parse_matrix, parse_ocr_lines

MAX_PAGES = 20
MAX_IMAGE_PIXELS = 28_000_000
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".pdf", ".xlsx", ".xls", ".docx"}


class DocumentRejected(ValueError):
    pass


def _safe_name(filename: str | None) -> str:
    name = Path(filename or "document").name
    name = re.sub(r"[\x00-\x1f/\\]+", "_", name).strip(" .")
    return (name or "document")[:180]


def _validate_archive(data: bytes, ext: str) -> None:
    if not data.startswith(b"PK"):
        raise DocumentRejected("ملف Office غير صالح.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            total = sum(item.file_size for item in archive.infolist())
            if len(names) > 2500 or total > 80 * 1024 * 1024:
                raise DocumentRejected("محتوى Office المضغوط يتجاوز الحد المسموح.")
            expected = "word/document.xml" if ext == ".docx" else "xl/workbook.xml"
            if expected not in names:
                raise DocumentRejected("امتداد الملف لا يطابق محتواه.")
    except zipfile.BadZipFile as exc:
        raise DocumentRejected("ملف Office تالف أو غير صالح.") from exc


def _order_page_points(points: np.ndarray) -> np.ndarray:
    points = points.astype("float32")
    ordered = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).reshape(-1)
    ordered[0] = points[np.argmin(sums)]       # top-left
    ordered[2] = points[np.argmax(sums)]       # bottom-right
    ordered[1] = points[np.argmin(differences)]  # top-right
    ordered[3] = points[np.argmax(differences)]  # bottom-left
    return ordered


def _crop_page(rgb: np.ndarray) -> np.ndarray:
    # Conservative four-corner page detection. If a clear quadrilateral is absent,
    # retain the full image rather than risk cutting off student names or grades.
    height, width = rgb.shape[:2]
    if min(height, width) < 500:
        return rgb
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 45, 140)
    edges = cv2.dilate(edges, np.ones((3, 3), dtype=np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            continue
        polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        area = cv2.contourArea(polygon)
        if len(polygon) != 4 or area < height * width * 0.18:
            continue
        points = _order_page_points(polygon.reshape(4, 2))
        tl, tr, br, bl = points
        target_width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
        target_height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
        if target_width < 350 or target_height < 450:
            continue
        target_width = min(target_width, 2400)
        target_height = min(target_height, 3400)
        destination = np.array(
            [[0, 0], [target_width - 1, 0],
             [target_width - 1, target_height - 1], [0, target_height - 1]],
            dtype="float32",
        )
        transform = cv2.getPerspectiveTransform(points, destination)
        cropped = cv2.warpPerspective(
            rgb, transform, (target_width, target_height),
            flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
        )
        return cropped
    return rgb


def _deskew_and_enhance(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise DocumentRejected("دقة الصورة أكبر من الحد المسموح.")
    if image.width > 2400:
        height = max(1, round(image.height * 2400 / image.width))
        image = image.resize((2400, height), Image.Resampling.LANCZOS)

    rgb = _crop_page(np.array(image))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, None, 8, 7, 21)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(binary > 0))
    if 1000 <= len(coords) <= 500_000:
        angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
        if angle < -45:
            angle = 90 + angle
        if 0.35 <= abs(angle) <= 7:
            h, w = gray.shape[:2]
            matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            gray = cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    cleaned = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 13
    )
    return Image.fromarray(cleaned)


def _ocr_lines(image: Image.Image) -> list[dict[str, Any]]:
    try:
        data = pytesseract.image_to_data(
            image,
            lang="ara+fra+eng",
            config="--oem 1 --psm 6 preserve_interword_spaces=1",
            output_type=pytesseract.Output.DICT,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError("Tesseract غير مثبت على الخادم.") from exc
    except pytesseract.TesseractError as exc:
        raise RuntimeError("تعذر تشغيل نماذج OCR؛ تحقق من تثبيت العربية والفرنسية.") from exc

    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for index, token_value in enumerate(data.get("text", [])):
        token = str(token_value or "").strip()
        if not token:
            continue
        try:
            confidence = max(0.0, min(1.0, float(data["conf"][index]) / 100.0))
        except (TypeError, ValueError):
            confidence = 0.0
        key = (int(data["block_num"][index]), int(data["par_num"][index]), int(data["line_num"][index]))
        grouped[key].append({
            "text": token, "confidence": confidence,
            "left": int(data["left"][index]), "top": int(data["top"][index]),
            "width": int(data["width"][index]), "height": int(data["height"][index]),
        })

    lines = []
    for line_no, tokens in enumerate(grouped.values(), start=1):
        tokens.sort(key=lambda item: item["left"])
        left = min(item["left"] for item in tokens)
        top = min(item["top"] for item in tokens)
        right = max(item["left"] + item["width"] for item in tokens)
        bottom = max(item["top"] + item["height"] for item in tokens)
        lines.append({
            "line": line_no,
            "tokens": [item["text"] for item in tokens],
            "text": " ".join(item["text"] for item in tokens),
            "confidence": sum(item["confidence"] for item in tokens) / len(tokens),
            "bbox": [left, top, right - left, bottom - top],
        })
    return lines


def _image_to_rows(image: Image.Image, filename: str, page: int):
    prepared = _deskew_and_enhance(image)
    return parse_ocr_lines(_ocr_lines(prepared), filename=filename, page_number=page)


def _process_xlsx(data: bytes, filename: str):
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    output, text_parts, warnings = [], [], []
    sheet_count = 0
    for sheet in workbook.worksheets:
        matrix = []
        for row in sheet.iter_rows(values_only=True):
            matrix.append(list(row))
            if len(matrix) >= 10000:
                warnings.append(f"توقفت قراءة ورقة {sheet.title} عند 10000 صف.")
                break
        rows, _header, notes = parse_matrix(matrix, filename=filename, source_type="xlsx", source_name=sheet.title)
        output.extend(rows)
        text_parts.extend(" ".join(str(x) for x in row if x is not None) for row in matrix[:30])
        warnings.extend(notes)
        sheet_count += 1
    workbook.close()
    return output, "\n".join(text_parts), warnings, max(sheet_count, 1)


def _process_xls(data: bytes, filename: str):
    book = xlrd.open_workbook(file_contents=data, on_demand=True)
    output, text_parts, warnings = [], [], []
    for sheet in book.sheets():
        matrix = [
            [sheet.cell_value(r, c) for c in range(sheet.ncols)]
            for r in range(min(sheet.nrows, 10000))
        ]
        rows, _header, notes = parse_matrix(matrix, filename=filename, source_type="xls", source_name=sheet.name)
        output.extend(rows)
        text_parts.extend(" ".join(str(x) for x in row if x not in (None, "")) for row in matrix[:30])
        warnings.extend(notes)
    count = max(len(book.sheet_names()), 1)
    book.release_resources()
    return output, "\n".join(text_parts), warnings, count


def _process_docx(data: bytes, filename: str):
    doc = Document(io.BytesIO(data))
    output = []
    text_parts = [paragraph.text for paragraph in doc.paragraphs if paragraph.text.strip()]
    warnings = []
    for index, table in enumerate(doc.tables, start=1):
        matrix = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        rows, _header, notes = parse_matrix(matrix, filename=filename, source_type="docx", source_name=f"جدول {index}")
        output.extend(rows)
        warnings.extend(notes)
    return output, "\n".join(text_parts), warnings, max(1, len(doc.tables))


def _process_pdf(data: bytes, filename: str):
    output, text_parts, warnings = [], [], []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages) > MAX_PAGES:
            raise DocumentRejected(f"ملف PDF يتجاوز الحد ({MAX_PAGES} صفحة).")
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            text_parts.append(text)
            tables = page.extract_tables() or []
            page_rows = []
            for table_index, table in enumerate(tables, start=1):
                rows, _header, notes = parse_matrix(
                    table, filename=filename, source_type="pdf_table",
                    source_name=f"الصفحة {index}، الجدول {table_index}",
                )
                page_rows.extend(rows)
                warnings.extend(notes)
            if page_rows:
                output.extend(page_rows)
            else:
                image = page.to_image(resolution=180).original
                rows, _header, ocr_text = _image_to_rows(image, filename, index)
                output.extend(rows)
                text_parts.append(ocr_text)
    return output, "\n".join(text_parts), warnings, len(pdf.pages)


def _process_image(data: bytes, filename: str):
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise DocumentRejected("الصورة تالفة أو غير مدعومة.") from exc
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise DocumentRejected("دقة الصورة أكبر من الحد المسموح.")
    frames = getattr(image, "n_frames", 1)
    if frames > MAX_PAGES:
        raise DocumentRejected(f"الصورة متعددة الإطارات تتجاوز الحد ({MAX_PAGES}).")
    output, text_parts = [], []
    for frame in range(frames):
        image.seek(frame)
        rows, _header, text = _image_to_rows(image.copy(), filename, frame + 1)
        output.extend(rows)
        text_parts.append(text)
    return output, "\n".join(text_parts), [], frames


def process_document(filename: str, data: bytes) -> dict[str, Any]:
    safe_name = Path(filename or "document").name
    safe_name = re.sub(r"[\x00-\x1f/\\]+", "_", safe_name).strip(" .")[:180] or "document"
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise DocumentRejected("صيغة الملف غير مدعومة. ارفع صورة أو PDF أو XLS/XLSX أو DOCX.")
    if not data:
        raise DocumentRejected("الملف فارغ.")

    if ext == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise DocumentRejected("امتداد PDF لا يطابق محتوى الملف.")
        rows, text, warnings, pages = _process_pdf(data, safe_name)
    elif ext in {".xlsx", ".docx"}:
        _validate_archive(data, ext)
        if ext == ".xlsx":
            rows, text, warnings, pages = _process_xlsx(data, safe_name)
        else:
            rows, text, warnings, pages = _process_docx(data, safe_name)
    elif ext == ".xls":
        if not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise DocumentRejected("امتداد XLS لا يطابق محتوى الملف.")
        rows, text, warnings, pages = _process_xls(data, safe_name)
    else:
        if ext != ".webp" and not data.startswith((
            b"\x89PNG", b"\xff\xd8", b"GIF8", b"BM", b"II*\x00", b"MM\x00*"
        )):
            raise DocumentRejected("امتداد الصورة لا يطابق محتواها.")
        rows, text, warnings, pages = _process_image(data, safe_name)

    fields = sorted({key for row in rows for key, value in row.get("grades", {}).items() if value is not None})
    classification = classify_document(text, rows, fields, structured=ext in {".xls", ".xlsx", ".docx", ".pdf"})
    return {
        "filename": safe_name,
        "pages": pages,
        "classification": classification,
        "rows": rows,
        "warnings": warnings,
    }
