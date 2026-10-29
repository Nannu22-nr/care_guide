import hashlib
import time
import json
from extraction import extract_text
from llm import extract_with_groq
from validation import normalize_confidence, validate_extraction, to_decimal
from db import (
    update_job, find_job_by_hash, find_duplicate_invoice,
    upsert_invoice_index, index_for_fts, add_event,
)


def make_duplicate_checker(current_job_id: str):
    def checker(file_hash: str, supplier: str, inv_no: str) -> dict:
        file_dup = False
        existing = find_job_by_hash(file_hash)
        if existing and existing["id"] != current_job_id:
            file_dup = True
        inv_dup = find_duplicate_invoice(supplier, inv_no, current_job_id) is not None
        return {"file_dup": file_dup, "invoice_dup": inv_dup}
    return checker


def _num(v):
    d = to_decimal(v)
    return float(d) if d is not None else None


def refresh_indexes(job_id: str, filename: str, raw_text: str, ext: dict):
    """Update the FTS + duplicate index for a job (also used after corrections)."""
    try:
        index_for_fts(job_id, filename, raw_text, json.dumps(ext, default=str))
    except Exception as e:
        add_event(job_id, "indexing", "warning", f"FTS index failed: {e}")
    try:
        upsert_invoice_index(
            job_id,
            ext.get("supplier_name"),
            ext.get("invoice_number"),
            _num(ext.get("total")),
            ext.get("invoice_date"),
        )
    except Exception as e:
        add_event(job_id, "indexing", "warning", f"Duplicate index failed: {e}")


def process_document(file_bytes: bytes, filename: str, job_id: str) -> dict:
    t0 = time.time()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    update_job(job_id, status="processing", stage="extracting_text")
    add_event(job_id, "extracting_text", "processing", "Text extraction / OCR started")
    try:
        raw_text = extract_text(file_bytes, filename)
    except Exception as e:
        return _fail(job_id, "EXTRACTION_FAILED", str(e), t0, file_hash)

    if raw_text.startswith("ERROR:"):
        return _fail(job_id, "EXTRACTION_FAILED", raw_text, t0, file_hash)

    add_event(job_id, "extracting_text", "done", f"{len(raw_text)} characters extracted")
    update_job(job_id, raw_text=raw_text, stage="llm_extraction")
    add_event(job_id, "llm_extraction", "processing", "LLM extraction started")

    try:
        ext = extract_with_groq(raw_text)
    except Exception as e:  # e.g. missing API key
        return _fail(job_id, "LLM_FAILED", str(e), t0, file_hash)
    if "_error" in ext:
        return _fail(job_id, "LLM_FAILED", ext["_error"], t0, file_hash, extraction=ext)

    ext = normalize_confidence(ext)
    update_job(job_id, extraction=json.dumps(ext, default=str), stage="validating")
    add_event(job_id, "llm_extraction", "done", "Structured fields extracted")

    checker = make_duplicate_checker(job_id)
    validation = validate_extraction(ext, raw_text, file_hash, duplicate_checker=checker)

    elapsed = round(time.time() - t0, 2)
    status = "needs_review" if validation["needs_review"] else "ok"
    update_job(
        job_id, status=status, stage="done",
        validation=json.dumps(validation, default=str),
        processing_time_sec=elapsed,
    )
    add_event(
        job_id, "validating", status,
        f"{len(validation['errors'])} error(s), {len(validation['flags'])} flag(s)",
    )

    refresh_indexes(job_id, filename, raw_text, ext)

    # Semantic index (optional, non-fatal)
    try:
        from embeddings import index_job_text
        index_job_text(job_id, raw_text)
    except Exception as e:
        add_event(job_id, "indexing", "warning", f"Semantic index skipped: {e}")

    add_event(job_id, "done", status, f"Finished in {elapsed}s")
    return {"job_id": job_id, "status": status, "file_hash": file_hash,
            "processing_time_sec": elapsed}


def _fail(job_id, rule, message, t0, file_hash, extraction=None):
    elapsed = round(time.time() - t0, 2)
    validation = {
        "errors": [{"rule": rule, "field": None, "message": message, "severity": "ERROR"}],
        "flags": [], "needs_review": True, "valid": False,
    }
    update_job(
        job_id, status="failed", stage="error", error=message,
        validation=json.dumps(validation, default=str),
        processing_time_sec=elapsed,
    )
    add_event(job_id, "error", "failed", f"{rule}: {message}")
    return {"job_id": job_id, "status": "failed", "error": message,
            "file_hash": file_hash, "processing_time_sec": elapsed}
