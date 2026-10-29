"""
AI-Powered Document Intelligence System - FastAPI application.

Defensive design:
- slowapi (rate limiting) is optional   -> falls back to a no-op decorator
- python-magic (MIME sniffing) optional -> falls back to extension-only checks
- sentence-transformers (semantic search) optional -> falls back to keyword search
"""

import hashlib
import json
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import config
from config import (
    BASE_DIR, UPLOAD_DIR, MAX_FILE_MB, RATE_LIMIT_PER_MINUTE,
    ENABLE_SEMANTIC_SEARCH, ALLOWED_EXTENSIONS, ALLOWED_ORIGINS, WORKER_THREADS,
)
from db import (
    init_db, create_job, get_job, list_jobs, count_jobs, find_job_by_hash,
    update_job, add_correction, get_corrections, get_stats, add_event, get_events,
    search_jobs_keyword, search_jobs_by_ids, delete_job,
)
from jobs import enqueue, start_workers, requeue_stuck_jobs
from auth import require_api_key
from validation import validate_extraction, normalize_confidence, to_decimal
from reconcile import reconcile_documents
from llm import INVOICE_FIELDS

# ---------------------------------------------------------------- optional deps
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    _HAS_SLOWAPI = True
except Exception:
    _HAS_SLOWAPI = False
    Limiter = None
    _rate_limit_exceeded_handler = None
    RateLimitExceeded = None
    get_remote_address = None

try:
    from security import validate_file_content
    _HAS_MAGIC = True
except Exception:
    _HAS_MAGIC = False

    def validate_file_content(data: bytes, filename: str) -> tuple[bool, str]:
        """Fallback: extension-only validation."""
        ext = Path(filename or "").suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            return False, f"Extension not allowed: {ext}"
        if len(data) == 0:
            return False, "Empty file"
        return True, "fallback"

try:
    from embeddings import semantic_search, load_index
    import embeddings as _emb
    _HAS_SEMANTIC = _emb.ENABLE_SEMANTIC_SEARCH
except Exception:
    _HAS_SEMANTIC = False

    def semantic_search(query: str, top_k: int = 20) -> list[str]:
        return []

    def load_index():
        return None


# ------------------------------------------------------------------- lifespan
@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    start_workers(n=WORKER_THREADS)
    try:
        requeue_stuck_jobs()
    except Exception:
        pass
    if _HAS_SEMANTIC:
        # Load persisted embeddings in the background so startup stays fast
        threading.Thread(target=_safe_load_index, daemon=True).start()
    yield


def _safe_load_index():
    try:
        load_index()
    except Exception as e:
        print(f"[semantic] index load failed: {e}")


app = FastAPI(title="AI Document Intelligence", version="2.1.0", lifespan=lifespan)

# ----------------------------------------------------------------- rate limit
if _HAS_SLOWAPI:
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
else:
    class _NoopLimiter:
        def limit(self, *args, **kwargs):
            def deco(fn):
                return fn
            return deco
    limiter = _NoopLimiter()


def rate_limit_str() -> str:
    return f"{RATE_LIMIT_PER_MINUTE}/minute"


# ----------------------------------------------------------------------- CORS
# Same-origin by default (the UI is served by this app). Cross-origin access
# must be opted into explicitly via ALLOWED_ORIGINS.
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# ------------------------------------------------------------------- helpers
def _safe_header_filename(name: str | None) -> str:
    """Strip characters that could break / inject into a Content-Disposition header."""
    base = os.path.basename(name or "file")
    return re.sub(r'[\r\n"\\]', "_", base) or "file"


def _job_to_json(job: dict | None) -> dict | None:
    if not job:
        return None
    out = dict(job)
    for k in ("extraction", "validation"):
        if out.get(k):
            try:
                out[k] = json.loads(out[k])
            except Exception:
                pass
    out.pop("file_path", None)
    out.pop("raw_text", None)
    return out


class CorrectionIn(BaseModel):
    field: str
    value: Any = None


_NUMERIC_FIELDS = {"subtotal", "tax", "total"}
_EDITABLE_FIELDS = set(INVOICE_FIELDS)


# --------------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/config")
def api_config():
    """Lets the UI know whether it must ask the user for an API key."""
    return {"auth_required": bool(config.API_KEY), "max_file_mb": MAX_FILE_MB,
            "semantic_search": _HAS_SEMANTIC}


# -------------------------------------------------------------------- upload
@app.post("/api/upload")
@limiter.limit(rate_limit_str())
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    _: None = Depends(require_api_key),
):
    if not files:
        raise HTTPException(400, "No files provided")

    max_bytes = MAX_FILE_MB * 1024 * 1024
    results = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        # Read at most max_bytes + 1 so an oversized upload is rejected
        # without pulling the whole thing into memory.
        data = await f.read(max_bytes + 1)
        if len(data) > max_bytes:
            results.append({
                "filename": f.filename,
                "error": f"File too large (> {MAX_FILE_MB}MB)",
            })
            continue

        ok, info = validate_file_content(data, f.filename or "")
        if not ok:
            results.append({"filename": f.filename, "error": info})
            continue

        file_hash = hashlib.sha256(data).hexdigest()
        existing = find_job_by_hash(file_hash)
        if existing:
            results.append({
                "job_id": existing["id"],
                "filename": f.filename,
                "status": existing["status"],
                "duplicate_of": existing["id"],
            })
            continue

        job_id = uuid.uuid4().hex
        dest = UPLOAD_DIR / f"{job_id}{ext}"
        dest.write_bytes(data)

        create_job(job_id, _safe_header_filename(f.filename), file_hash, str(dest), len(data))
        enqueue(job_id)

        results.append({"job_id": job_id, "filename": f.filename, "status": "queued"})

    return {"results": results}


# ------------------------------------------------------------ jobs list/detail
@app.get("/api/jobs")
def api_list_jobs(limit: int = 50, offset: int = 0, status: str | None = None,
                  _: None = Depends(require_api_key)):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return {
        "jobs": list_jobs(limit=limit, offset=offset, status=status),
        "total": count_jobs(status=status),
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str, _: None = Depends(require_api_key)):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    payload = _job_to_json(job)
    payload["corrections"] = get_corrections(job_id)
    payload["events"] = get_events(job_id)
    return payload


@app.get("/api/jobs/{job_id}/events")
def api_job_events(job_id: str, _: None = Depends(require_api_key)):
    if not get_job(job_id):
        raise HTTPException(404, "Job not found")
    return {"job_id": job_id, "events": get_events(job_id)}


@app.delete("/api/jobs/{job_id}")
def api_delete_job(job_id: str, _: None = Depends(require_api_key)):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    delete_job(job_id)
    return {"ok": True, "message": "Job and file deleted successfully"}


# --------------------------------------------------------- file preview/download
def _file_response(job_id: str, disposition: str):
    job = get_job(job_id, include_raw=True)
    if not job:
        raise HTTPException(404, "Job not found")
    path = job.get("file_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "File not found on disk")
    name = _safe_header_filename(job.get("filename"))
    return FileResponse(path, headers={
        "Content-Disposition": f'{disposition}; filename="{name}"',
        "X-Content-Type-Options": "nosniff",
    })


@app.get("/api/files/{job_id}")
def api_get_file(job_id: str, _: None = Depends(require_api_key)):
    return _file_response(job_id, "inline")


@app.get("/api/download/{job_id}")
def api_download_file(job_id: str, _: None = Depends(require_api_key)):
    return _file_response(job_id, "attachment")


# -------------------------------------------------------------------- search
@app.get("/api/search")
def api_search(q: str = "", mode: str = "keyword", limit: int = 50, offset: int = 0,
               status: str | None = None, supplier: str | None = None,
               _: None = Depends(require_api_key)):
    """Keyword (FTS5) or semantic search, with optional status/supplier filters."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    if not q.strip() and not status and not (supplier or "").strip():
        return {"jobs": [], "mode": mode, "total": 0}

    if mode == "semantic" and q.strip() and _HAS_SEMANTIC:
        ids = semantic_search(q, top_k=limit + offset + 50)
        jobs = search_jobs_by_ids(ids, limit=len(ids)) if ids else []
        if status:
            jobs = [j for j in jobs if j["status"] == status]
        order = {jid: i for i, jid in enumerate(ids)}
        jobs.sort(key=lambda j: order.get(j["id"], 9999))
        page = jobs[offset:offset + limit]
        return {"jobs": page, "mode": "semantic", "total": len(jobs)}

    jobs = search_jobs_keyword(q, limit=limit, offset=offset, status=status, supplier=supplier)
    resp = {"jobs": jobs, "mode": "keyword", "total": len(jobs)}
    if mode == "semantic" and not _HAS_SEMANTIC:
        resp["note"] = "Semantic search is unavailable; fell back to keyword search."
    return resp


# ---------------------------------------------------------------- corrections
@app.post("/api/jobs/{job_id}/correct")
@limiter.limit(rate_limit_str())
async def api_correct(
    request: Request,
    job_id: str,
    payload: CorrectionIn,
    _: None = Depends(require_api_key),
):
    from pipeline import make_duplicate_checker, refresh_indexes

    job = get_job(job_id, include_raw=True)
    if not job:
        raise HTTPException(404, "Job not found")

    field, new_value = payload.field, payload.value
    if field not in _EDITABLE_FIELDS:
        raise HTTPException(400, f"Unknown field '{field}'")

    if field in _NUMERIC_FIELDS and new_value not in (None, ""):
        dec = to_decimal(new_value)
        if dec is None:
            raise HTTPException(400, f"'{field}' must be a number")
        new_value = float(dec)
    elif new_value == "":
        new_value = None

    try:
        ext = json.loads(job["extraction"]) if job["extraction"] else {}
    except Exception:
        ext = {}

    old_value = ext.get(field)
    ext[field] = new_value

    conf = ext.get("confidence") or {}
    conf[field] = 1.0 if new_value is not None else 0.0
    ext["confidence"] = conf
    ext = normalize_confidence(ext)

    validation = validate_extraction(
        ext,
        job.get("raw_text") or "",
        job.get("file_hash") or "",
        duplicate_checker=make_duplicate_checker(job_id),
    )
    new_status = "needs_review" if validation["needs_review"] else "ok"

    update_job(
        job_id,
        extraction=json.dumps(ext, default=str),
        validation=json.dumps(validation, default=str),
        status=new_status,
        error=None,
    )
    add_correction(job_id, field, old_value, new_value)
    add_event(job_id, "correction", new_status, f"{field}: {old_value!r} -> {new_value!r}")
    # keep search + duplicate detection in sync with the corrected values
    refresh_indexes(job_id, job["filename"], job.get("raw_text") or "", ext)

    return {"ok": True, "extraction": ext, "validation": validation, "status": new_status}


# ------------------------------------------------------------------ reprocess
@app.post("/api/jobs/{job_id}/reprocess")
def api_reprocess(job_id: str, _: None = Depends(require_api_key)):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] in ("queued", "processing"):
        raise HTTPException(409, "Job is already queued or processing")

    update_job(job_id, status="queued", stage="queued", error=None,
               extraction=None, validation=None)
    add_event(job_id, "queued", "queued", "Reprocess requested")
    enqueue(job_id)
    return {"ok": True, "job_id": job_id, "status": "queued"}


@app.post("/api/reprocess-failed")
def api_reprocess_failed(_: None = Depends(require_api_key)):
    """Re-queue every failed job in one call."""
    ids = [j["id"] for j in list_jobs(limit=1000, status="failed")]
    for jid in ids:
        update_job(jid, status="queued", stage="queued", error=None,
                   extraction=None, validation=None)
        add_event(jid, "queued", "queued", "Bulk reprocess of failed jobs")
        enqueue(jid)
    return {"ok": True, "requeued": len(ids), "job_ids": ids}


# ------------------------------------------------------------ cross-verification
@app.post("/api/reconcile")
async def api_reconcile(payload: dict, _: None = Depends(require_api_key)):
    if payload.get("all"):
        ids = [j["id"] for j in list_jobs(limit=500)
               if j["status"] in ("ok", "needs_review")]
    else:
        ids = payload.get("job_ids") or []

    if len(ids) < 2:
        raise HTTPException(400, "Need at least 2 job_ids or all=true")

    docs = []
    for jid in ids:
        j = get_job(jid)
        if not j:
            continue
        ext = j.get("extraction") or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except Exception:
                ext = {}
        docs.append({"job_id": j["id"], "filename": j["filename"], "extraction": ext})

    return reconcile_documents(docs)


# ------------------------------------------------------------- stats & health
@app.get("/api/stats")
def api_stats(_: None = Depends(require_api_key)):
    return get_stats()


@app.get("/api/health")
def health():
    checks = {"api": "ok"}
    try:
        from db import get_db
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"

    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        checks["tesseract"] = "ok"
    except Exception as e:
        checks["tesseract"] = f"error: {str(e)[:80]}"

    checks["llm"] = "ok" if config.GROQ_API_KEY else "missing_key"
    checks["auth"] = "enabled" if config.API_KEY else "disabled"
    checks["rate_limit"] = "ok" if _HAS_SLOWAPI else "disabled"
    checks["mime_sniff"] = "ok" if _HAS_MAGIC else "disabled"
    checks["semantic_search"] = "ok" if _HAS_SEMANTIC else "disabled"

    overall = "ok" if all(
        v in ("ok", "disabled", "missing_key", "enabled") for v in checks.values()
    ) else "degraded"
    return {"status": overall, "checks": checks}
