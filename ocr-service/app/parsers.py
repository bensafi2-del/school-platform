from __future__ import annotations

import re
import unicodedata
from typing import Any

from openpyxl.utils import get_column_letter


FIELD_PATTERNS = {
    "cc": (
        "التقويم المستمر", "المراقبة المستمرة", "مراقبة مستمرة",
        "controle continu", "contrôle continu", "continuous assessment",
    ),
    "f1": (
        "الفرض الاول", "الفرض 1", "فرض 1", "devoir 1", "1er devoir",
        "premier devoir", "controle 1", "contrôle 1",
    ),
    "f2": (
        "الفرض الثاني", "الفرض 2", "فرض 2", "devoir 2", "2eme devoir",
        "deuxieme devoir", "deuxième devoir", "controle 2", "contrôle 2",
    ),
    "ex": (
        "الاختبار", "الامتحان", "اختبار", "امتحان", "examen",
        "epreuve", "épreuve", "composition", "test final",
    ),
}

NUMBER_LABELS = (
    "رقم الترتيب", "رقم", "الرقم", "n ordre", "n°", "numero",
    "numéro", "matricule", "ordre", "no",
)
FIRST_LABELS = ("الاسم", "prenom", "prénom", "first name")
LAST_LABELS = ("اللقب", "النسب", "nom", "last name", "family name")
FULL_NAME_LABELS = (
    "الاسم واللقب", "اللقب والاسم", "اسم التلميذ", "التلميذ",
    "nom et prenom", "nom et prénom", "prenom et nom", "prénom et nom",
    "nom complet", "full name", "student",
)
GRADE_TERMS = (
    "كشف النقاط", "كشف العلامات", "جدول العلامات", "النتائج الدراسية",
    "bulletin", "releve de notes", "relevé de notes", "notes",
    "moyenne", "evaluation", "évaluation", "التقويم", "العلامات",
)

DIGIT_TRANSLATION = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789..")
NUMBER_RE = re.compile(r"(?<![\w])(?:\d{1,2}(?:[.,]\d{1,2})?)(?![\w])")


def normalize_digits(value: Any) -> str:
    return str(value if value is not None else "").translate(DIGIT_TRANSLATION)


def normalize_label(value: Any) -> str:
    value = unicodedata.normalize("NFKD", normalize_digits(value)).casefold()
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("ـ", "")
    value = re.sub(r"[أإآٱ]", "ا", value)
    value = re.sub(r"[ىي]", lambda m: "ي" if m.group(0) in "ىي" else m.group(0), value)
    value = re.sub(r"[\u064b-\u065f\u0670]", "", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def detect_grade_field(label: Any) -> str | None:
    raw = normalize_label(label)
    if not raw:
        return None
    # More specific labels are tested before short abbreviations.
    for field in ("cc", "f1", "f2", "ex"):
        if any(normalize_label(term) in raw for term in FIELD_PATTERNS[field]):
            return field
    compact = re.sub(r"\s+", "", raw)
    if re.search(r"(^|[^a-z0-9])cc([^a-z0-9]|$)", raw) or compact == "cc":
        return "cc"
    if re.search(r"(^|[^a-z0-9])f1([^a-z0-9]|$)", raw) or compact == "f1":
        return "f1"
    if re.search(r"(^|[^a-z0-9])f2([^a-z0-9]|$)", raw) or compact == "f2":
        return "f2"
    if re.search(r"(^|[^a-z0-9])ex([^a-z0-9]|$)", raw) or compact == "ex":
        return "ex"
    return None


def _contains_any(text: str, labels: tuple[str, ...]) -> bool:
    return any(normalize_label(item) in text for item in labels)


def _number(value: Any) -> int | None:
    text = normalize_digits(value).strip()
    if not text:
        return None
    match = re.search(r"\d{1,4}", text)
    if not match:
        return None
    try:
        result = int(match.group(0))
    except ValueError:
        return None
    return result if result > 0 else None


def parse_grade(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        text = normalize_digits(value).strip().replace(",", ".")
        if text in {"", "-", "—", "–", "/", "غياب", "abs"}:
            return None
        match = re.search(r"(?<!\d)(\d{1,2}(?:\.\d{1,2})?)(?!\d)", text)
        if not match:
            return None
        try:
            result = float(match.group(1))
        except ValueError:
            return None
    if result < 0 or result > 20:
        return None
    return round(result, 2)


def _header_info(row: list[Any]) -> dict[str, Any]:
    columns: dict[str, int] = {}
    first_col = last_col = full_name_col = number_col = None

    for index, cell in enumerate(row):
        label = normalize_label(cell)
        if not label:
            continue
        field = detect_grade_field(cell)
        if field and field not in columns:
            columns[field] = index
        if number_col is None and _contains_any(label, tuple(normalize_label(x) for x in NUMBER_LABELS)):
            number_col = index
        if full_name_col is None and _contains_any(label, tuple(normalize_label(x) for x in FULL_NAME_LABELS)):
            full_name_col = index
        if first_col is None and _contains_any(label, tuple(normalize_label(x) for x in FIRST_LABELS)):
            first_col = index
        if last_col is None and _contains_any(label, tuple(normalize_label(x) for x in LAST_LABELS)):
            last_col = index

    has_name = full_name_col is not None or (first_col is not None and last_col is not None)
    score = len(columns) * 2 + (2 if has_name else 0) + (1 if number_col is not None else 0)
    return {
        "columns": columns,
        "number_col": number_col,
        "full_name_col": full_name_col,
        "first_col": first_col,
        "last_col": last_col,
        "score": score,
        "has_name": has_name,
    }


def parse_matrix(
    matrix: list[list[Any]],
    *,
    filename: str,
    source_type: str,
    source_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    warnings: list[str] = []
    best: tuple[int, dict[str, Any]] | None = None
    for index, row in enumerate(matrix[:30]):
        info = _header_info(list(row))
        if info["columns"] and (info["has_name"] or info["number_col"] is not None):
            if best is None or info["score"] > best[1]["score"]:
                best = (index, info)
    if best is None:
        return [], {"fields": [], "header_found": False}, [
            "لم أتعرف على صف العناوين تلقائيًا؛ راجع المستند أو أدخل العلامات يدويًا."
        ]

    header_index, info = best
    fields = [key for key, _ in sorted(info["columns"].items(), key=lambda pair: pair[1])]
    rows: list[dict[str, Any]] = []

    for row_index in range(header_index + 1, len(matrix)):
        values = list(matrix[row_index])
        if not any(value is not None and str(value).strip() for value in values):
            continue

        def at(index: int | None) -> Any:
            return values[index] if index is not None and index < len(values) else None

        if info["full_name_col"] is not None:
            raw_name = str(at(info["full_name_col"]) or "").strip()
        else:
            first = str(at(info["first_col"]) or "").strip()
            last = str(at(info["last_col"]) or "").strip()
            raw_name = " ".join(part for part in (first, last) if part)

        list_number = _number(at(info["number_col"]))
        grades: dict[str, float | None] = {}
        sources: dict[str, dict[str, Any]] = {}
        for field, col_index in info["columns"].items():
            grades[field] = parse_grade(at(col_index))
            sources[field] = {
                "file": filename,
                "type": source_type,
                "location": source_name,
                "row": row_index + 1,
                "cell": f"{get_column_letter(col_index + 1)}{row_index + 1}",
            }

        if not raw_name and list_number is None:
            continue
        if not any(value is not None for value in grades.values()):
            continue

        rows.append({
            "list_number": list_number,
            "raw_name": raw_name,
            "grades": grades,
            "sources": sources,
            "confidence": 0.96,
            "source": {
                "file": filename,
                "type": source_type,
                "location": source_name,
                "row": row_index + 1,
            },
            "warnings": [],
        })

    if not rows:
        warnings.append("تم العثور على عناوين أعمدة، لكن لم تُستخرج صفوف تحتوي علامات قابلة للقراءة.")
    return rows, {
        "fields": fields,
        "header_found": True,
        "header_row": header_index + 1,
        "score": info["score"],
    }, warnings


def parse_ocr_lines(
    lines: list[dict[str, Any]],
    *,
    filename: str,
    page_number: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    full_text = "\n".join(item.get("text", "") for item in lines)
    ordered_fields: list[str] = []
    for line in lines:
        tokens = line.get("tokens", [])
        for token in tokens:
            field = detect_grade_field(token)
            if field and field not in ordered_fields:
                ordered_fields.append(field)
        # Some multi-word headers are split into adjacent OCR tokens.
        for field in ("cc", "f1", "f2", "ex"):
            if field not in ordered_fields and detect_grade_field(line.get("text", "")) == field:
                ordered_fields.append(field)

    rows: list[dict[str, Any]] = []
    for line in lines:
        text = normalize_digits(line.get("text", "")).strip()
        if not text:
            continue
        matches = list(NUMBER_RE.finditer(text))
        numeric_values: list[float] = []
        for match in matches:
            value = parse_grade(match.group(0))
            if value is not None:
                numeric_values.append(value)
        if len(numeric_values) < 2:
            continue

        list_number = None
        mapped_values = numeric_values[:]
        if ordered_fields and len(mapped_values) > len(ordered_fields):
            first = matches[0].group(0) if matches else ""
            candidate_no = _number(first)
            if candidate_no is not None and len(mapped_values) >= len(ordered_fields) + 1:
                list_number = candidate_no
                mapped_values = mapped_values[-len(ordered_fields):]

        grades: dict[str, float | None] = {field: None for field in ordered_fields}
        sources: dict[str, dict[str, Any]] = {}
        if ordered_fields and len(mapped_values) >= len(ordered_fields):
            selected = mapped_values[-len(ordered_fields):]
            for field, value in zip(ordered_fields, selected):
                grades[field] = value
                sources[field] = {
                    "file": filename,
                    "type": "ocr",
                    "page": page_number,
                    "line": line.get("line"),
                    "bbox": line.get("bbox"),
                }

        # Remove detected numbers and tidy the remaining name text.
        raw_name = NUMBER_RE.sub(" ", text)
        raw_name = re.sub(r"\s+", " ", raw_name).strip(" |:;،.-–—")
        if not raw_name and list_number is None:
            continue
        confidence = max(0.0, min(0.99, float(line.get("confidence", 0.45))))
        rows.append({
            "list_number": list_number,
            "raw_name": raw_name,
            "grades": grades,
            "candidate_values": numeric_values,
            "sources": sources,
            "confidence": round(confidence, 2),
            "source": {
                "file": filename,
                "type": "ocr",
                "page": page_number,
                "line": line.get("line"),
                "bbox": line.get("bbox"),
            },
            "warnings": [] if ordered_fields else [
                "لم تُكتشف عناوين أعمدة العلامات؛ أدخل القيم يدويًا بعد مراجعة الصورة."
            ],
        })

    return rows, {"fields": ordered_fields, "header_found": bool(ordered_fields)}, full_text[:12000]


def classify_document(
    text: str,
    rows: list[dict[str, Any]],
    fields: list[str],
    *,
    structured: bool,
) -> dict[str, Any]:
    normalized = normalize_label(text)
    anchors = [term for term in GRADE_TERMS if normalize_label(term) in normalized]
    reasons: list[str] = []
    if anchors:
        reasons.append("ظهرت كلمات مرتبطة بالعلامات أو كشف النقاط.")
    if fields:
        reasons.append("تم التعرف على أعمدة تقييم: " + "، ".join(fields) + ".")
    if rows:
        reasons.append(f"استُخرجت {len(rows)} صفوف تحتوي بيانات مرشحة.")
    if not anchors and not fields:
        reasons.append("لم تظهر مؤشرات كافية تؤكد أن المستند كشف علامات.")
    score = 0.12
    score += min(len(fields), 4) * 0.13
    score += 0.22 if anchors else 0.0
    score += 0.18 if rows else 0.0
    score += 0.08 if structured else 0.0
    score = round(min(0.99, score), 2)
    is_grade_sheet = bool(rows) and (bool(fields) or bool(anchors))
    return {
        "is_grade_sheet": is_grade_sheet,
        "confidence": score,
        "reasons": reasons,
        "detected_fields": fields,
        "review_required": score < 0.72 or not is_grade_sheet,
    }
