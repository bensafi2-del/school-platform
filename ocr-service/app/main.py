from __future__ import annotations

import os
from typing import Any
from uuid import UUID
from urllib.parse import quote

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .processing import DocumentRejected, process_document
from .parsers import normalize_label

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(15 * 1024 * 1024)))
MAX_TOTAL_BYTES = int(os.getenv("MAX_TOTAL_BYTES", str(45 * 1024 * 1024)))
MAX_FILES = int(os.getenv("MAX_FILES", "8"))
CORS_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip()]

app = FastAPI(
    title="School Platform — Smart Grade Import",
    version="0.1.0",
    docs_url="/docs" if os.getenv("ENABLE_API_DOCS", "false").lower() == "true" else None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "apikey"],
)


def _headers(token: str) -> dict[str, str]:
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        raise HTTPException(503, "لم تُضبط متغيرات اتصال Supabase في خدمة OCR.")
    return {
        "apikey": SUPABASE_PUBLISHABLE_KEY,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def _supabase_json(
    client: httpx.AsyncClient,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
):
    url = f"{SUPABASE_URL}/{path.lstrip('/')}"
    headers = _headers(token)
    response = await (
        client.get(url, headers=headers)
        if body is None
        else client.post(url, headers=headers, json=body)
    )
    if response.status_code >= 400:
        if response.status_code in (401, 403):
            raise HTTPException(response.status_code, "انتهت الجلسة أو لا تملك صلاحية استيراد هذا الفوج والمادة.")
        raise HTTPException(502, "تعذر التحقق من الصلاحيات في Supabase.")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(502, "استجابة غير صالحة من Supabase.") from exc


async def authorized_roster(
    token: str, school_id: str, group_id: str, subject_id: str
) -> list[dict[str, Any]]:
    timeout = httpx.Timeout(20.0, connect=8.0)
    try:
        school_id = str(UUID(school_id))
        group_id = str(UUID(group_id))
        subject_id = str(UUID(subject_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "معرّفات المؤسسة والفوج والمادة غير صالحة.") from exc

    async with httpx.AsyncClient(timeout=timeout) as client:
        user = await _supabase_json(client, "/auth/v1/user", token)
        if not isinstance(user, dict) or not user.get("id"):
            raise HTTPException(401, "جلسة المستخدم غير صالحة.")

        # Managers may access several institutions, so confirm that group and subject
        # really belong to the requested school. Teachers must hold the exact triple assignment.
        is_manager = await _supabase_json(
            client, "/rest/v1/rpc/is_school_manager", token, {"sid": school_id}
        )
        if is_manager is True:
            groups = await _supabase_json(
                client,
                f"/rest/v1/school_groups?select=id,school_id&id=eq.{quote(group_id)}&school_id=eq.{quote(school_id)}",
                token,
            )
            subjects = await _supabase_json(
                client,
                f"/rest/v1/subjects?select=id,school_id&id=eq.{quote(subject_id)}&school_id=eq.{quote(school_id)}",
                token,
            )
            if not groups or not subjects:
                raise HTTPException(403, "الفوج أو المادة لا ينتميان إلى المؤسسة المختارة.")
        else:
            assignments = await _supabase_json(
                client, "/rest/v1/rpc/my_assignments", token, {}
            )
            exact_assignment = any(
                str(item.get("a_school")) == school_id
                and str(item.get("a_group")) == group_id
                and str(item.get("a_subject")) == subject_id
                for item in (assignments or [])
            )
            if not exact_assignment:
                raise HTTPException(403, "هذا التعيين غير موجود ضمن موادك وأفواجك.")

        allowed = await _supabase_json(
            client,
            "/rest/v1/rpc/can_grade",
            token,
            {"p_school": school_id, "p_group": group_id, "p_subject": subject_id},
        )
        if allowed is not True:
            raise HTTPException(403, "لا تملك صلاحية إدخال علامات هذه المادة في هذا الفوج.")

        roster = await _supabase_json(
            client,
            "/rest/v1/rpc/teacher_group_roster",
            token,
            {"p_group": group_id},
        )
        if not isinstance(roster, list):
            raise HTTPException(502, "تعذر قراءة قائمة التلاميذ.")
        return roster


def _name_key(value: Any) -> str:
    return normalize_label(str(value or ""))


def _match_rows(rows: list[dict[str, Any]], roster: list[dict[str, Any]]) -> None:
    by_number: dict[int, list[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}

    for student in roster:
        try:
            number = int(student.get("r_no"))
            by_number.setdefault(number, []).append(student)
        except (TypeError, ValueError):
            pass
        first = str(student.get("r_first") or "")
        last = str(student.get("r_last") or "")
        for candidate in (first + " " + last, last + " " + first):
            key = _name_key(candidate)
            if key:
                by_name.setdefault(key, []).append(student)

    for row in rows:
        raw_name = str(row.get("raw_name") or "").strip()
        name_key = _name_key(raw_name)
        name_matches = by_name.get(name_key, []) if name_key else []
        name_matches = list({s.get("r_student"): s for s in name_matches}.values())
        number = row.get("list_number")
        number_matches = by_number.get(number, []) if isinstance(number, int) else []

        suggestion = None
        status = "unmatched"
        reasons = []
        if name_matches and number_matches:
            intersection = [
                s for s in name_matches
                if any(n.get("r_student") == s.get("r_student") for n in number_matches)
            ]
            if len(intersection) == 1:
                suggestion, status = intersection[0], "exact_name_and_number"
            else:
                status = "conflict"
                reasons.append("الاسم ورقم الترتيب لا يشيران إلى التلميذ نفسه.")
        elif name_matches and len(name_matches) == 1 and number is None:
            suggestion, status = name_matches[0], "exact_name"
        elif number_matches and len(number_matches) == 1 and not raw_name:
            status = "needs_review"
            reasons.append("وُجد رقم الترتيب دون اسم؛ يلزم التأكيد يدويًا.")
        elif number_matches or name_matches:
            status = "needs_review"
            reasons.append("توجد مطابقة غير فريدة أو جزئية؛ اختر التلميذ يدويًا.")
        else:
            reasons.append("لم توجد مطابقة حرفية مؤكدة؛ اختر التلميذ يدويًا.")

        row["suggested_student_id"] = suggestion.get("r_student") if suggestion else None
        row["match_status"] = status
        row["match_reasons"] = reasons
        candidates = name_matches or number_matches
        row["candidates"] = [
            {
                "student_id": s.get("r_student"),
                "list_number": s.get("r_no"),
                "first_name": s.get("r_first"),
                "last_name": s.get("r_last"),
            }
            for s in candidates[:5]
        ]


@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "smart-grade-import",
        "version": "0.1.0",
        "tesseract_required_languages": ["ara", "fra", "eng"],
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY),
    }


@app.post("/api/v1/grade-import/preview")
async def grade_import_preview(
    school_id: str = Form(...),
    group_id: str = Form(...),
    subject_id: str = Form(...),
    files: list[UploadFile] = File(...),
    authorization: str | None = Header(default=None),
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "سجّل الدخول أولًا.")
    token = authorization.split(" ", 1)[1].strip()
    if not token or len(token) > 10000:
        raise HTTPException(401, "رمز الجلسة غير صالح.")
    if not school_id or not group_id or not subject_id:
        raise HTTPException(422, "اختر المؤسسة والفوج والمادة.")
    if not files:
        raise HTTPException(422, "اختر ملفًا واحدًا على الأقل.")
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"الحد الأقصى {MAX_FILES} ملفات في المرة الواحدة.")

    roster = await authorized_roster(token, school_id, group_id, subject_id)
    if not roster:
        raise HTTPException(422, "لا يوجد تلاميذ مسجلون في الفوج المختار.")

    documents = []
    total_bytes = 0
    for upload in files:
        content = await upload.read(MAX_FILE_BYTES + 1)
        total_bytes += len(content)
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(413, f"الملف {upload.filename or ''} يتجاوز الحد المسموح ({MAX_FILE_BYTES // (1024 * 1024)} MB).")
        if total_bytes > MAX_TOTAL_BYTES:
            raise HTTPException(413, "مجموع الملفات يتجاوز الحد المسموح.")
        try:
            document = await run_in_threadpool(
                process_document, upload.filename or "document", content
            )
        except DocumentRejected as exc:
            raise HTTPException(422, f"{upload.filename or 'الملف'}: {exc}") from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422, f"تعذر تحليل الملف {upload.filename or ''}. تحقق من سلامته وصيغته.") from exc
        finally:
            await upload.close()
        documents.append(document)

    all_rows = [row for document in documents for row in document["rows"]]
    _match_rows(all_rows, roster)

    seen: dict[str, int] = {}
    for row in all_rows:
        student_id = row.get("suggested_student_id")
        if student_id:
            seen[student_id] = seen.get(student_id, 0) + 1
    for row in all_rows:
        student_id = row.get("suggested_student_id")
        if student_id and seen.get(student_id, 0) > 1:
            row["match_status"] = "duplicate_in_import"
            row["suggested_student_id"] = None
            row.setdefault("match_reasons", []).append("ظهر التلميذ أكثر من مرة في الملفات المرفوعة.")

    return JSONResponse({
        "ok": True,
        "temporary": True,
        "stored": False,
        "student_count": len(roster),
        "document_count": len(documents),
        "row_count": len(all_rows),
        "students": [
            {
                "student_id": student.get("r_student"),
                "list_number": student.get("r_no"),
                "first_name": student.get("r_first"),
                "last_name": student.get("r_last"),
            }
            for student in roster
        ],
        "documents": documents,
        "rows": all_rows,
        "notice": "هذه معاينة مؤقتة. لا تُحفظ أي علامة في قاعدة البيانات قبل اعتماد المستخدم من الواجهة.",
    })
