import copy
import json

import pytest

import pipeline
from tests.conftest import GOOD_LLM_RESULT
from db import create_job, get_job, get_events


def _run(monkeypatch, llm_result, content, name="inv.csv", job_id=None):
    import hashlib
    job_id = job_id or hashlib.md5(content).hexdigest()
    create_job(job_id, name, hashlib.sha256(content).hexdigest(), "/tmp/x", len(content))
    monkeypatch.setattr(pipeline, "extract_with_groq", lambda text: copy.deepcopy(llm_result))
    res = pipeline.process_document(content, name, job_id)
    return job_id, res, get_job(job_id)


def test_happy_path_ok(monkeypatch, unique):
    content = f"invoice_number,total\nINV-{unique},990\n".encode()
    res = copy.deepcopy(GOOD_LLM_RESULT)
    res["invoice_number"] = f"INV-{unique}"
    _, out, job = _run(monkeypatch, res, content)
    assert out["status"] == "ok"
    assert job["stage"] == "done"
    assert json.loads(job["validation"])["valid"] is True


def test_total_mismatch_goes_to_review(monkeypatch, unique):
    content = f"a,b\n{unique},1\n".encode()
    res = copy.deepcopy(GOOD_LLM_RESULT)
    res["invoice_number"] = f"INV-{unique}"
    res["total"] = 5000.0
    _, out, job = _run(monkeypatch, res, content)
    assert out["status"] == "needs_review"
    rules = [e["rule"] for e in json.loads(job["validation"])["errors"]]
    assert "TOTAL_MISMATCH" in rules


def test_missing_values_stay_null_not_invented(monkeypatch, unique):
    content = f"x\n{unique}\n".encode()
    res = {"invoice_number": None, "invoice_date": None, "supplier_name": None,
           "customer_name": None, "subtotal": None, "tax": None, "total": None,
           "currency": None, "line_items": None}
    _, out, job = _run(monkeypatch, res, content)
    ext = json.loads(job["extraction"])
    assert ext["invoice_number"] is None and ext["total"] is None
    assert out["status"] == "needs_review"


def test_llm_error_marks_job_failed(monkeypatch, unique):
    content = f"x\n{unique}\n".encode()
    _, out, job = _run(monkeypatch, {"_error": "rate limited"}, content)
    assert out["status"] == "failed"
    assert job["status"] == "failed" and "rate limited" in job["error"]


def test_corrupt_file_marks_job_failed(monkeypatch, unique):
    content = b"%PDF-garbage-" + unique.encode()
    _, out, job = _run(monkeypatch, GOOD_LLM_RESULT, content, name="broken.pdf")
    assert out["status"] == "failed"
    assert "EXTRACTION_FAILED" in job["validation"]


def test_duplicate_invoice_detected(monkeypatch, unique):
    res = copy.deepcopy(GOOD_LLM_RESULT)
    res["invoice_number"] = f"DUP-{unique}"
    _run(monkeypatch, res, f"first {unique}\n".encode(), name="one.csv")
    _, out, job = _run(monkeypatch, res, f"second {unique}\n".encode(), name="two.csv")
    rules = [e["rule"] for e in json.loads(job["validation"])["errors"]]
    assert "DUPLICATE_INVOICE" in rules
    assert out["status"] == "needs_review"


def test_processing_history_recorded(monkeypatch, unique):
    res = copy.deepcopy(GOOD_LLM_RESULT)
    res["invoice_number"] = f"H-{unique}"
    job_id, _, _ = _run(monkeypatch, res, f"hist {unique}\n".encode())
    stages = [e["stage"] for e in get_events(job_id)]
    for s in ("queued", "extracting_text", "llm_extraction", "validating", "done"):
        assert s in stages
