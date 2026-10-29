"""Failure scenarios at the API boundary."""
import pytest
from fastapi.testclient import TestClient

import auth
from app import app

client = TestClient(app)


def _post(name, data, mime="application/octet-stream"):
    return client.post("/api/upload", files={"files": (name, data, mime)})


def test_empty_file_rejected():
    r = _post("e.csv", b"", "text/csv")
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert "error" in res and "job_id" not in res


def test_disallowed_extension_rejected():
    res = _post("virus.exe", b"MZ\x90\x00").json()["results"][0]
    assert "error" in res and "job_id" not in res


def test_fake_pdf_content_rejected_by_mime_sniff():
    # a PNG renamed to .pdf must not be accepted as a PDF
    png_magic = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    res = _post("fake.pdf", png_magic, "application/pdf").json()["results"][0]
    assert "error" in res


def test_oversized_file_rejected(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "MAX_FILE_MB", 0)   # everything is "too large"
    res = _post("big.csv", b"a,b\n1,2\n", "text/csv").json()["results"][0]
    assert "too large" in res["error"].lower()


def test_duplicate_upload_returns_same_job(unique):
    csv = f"invoice_number,supplier,total\nINV-{unique},ACME,50\n".encode()
    r1 = _post("d.csv", csv, "text/csv").json()["results"][0]
    r2 = _post("d.csv", csv, "text/csv").json()["results"][0]
    assert r1["job_id"] == r2["job_id"]
    assert r2.get("duplicate_of") == r1["job_id"]


def test_api_key_enforced_when_configured(monkeypatch):
    monkeypatch.setattr(auth.config, "API_KEY", "secret123")
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/jobs", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/jobs", headers={"X-API-Key": "secret123"}).status_code == 200
    # query-param form is needed for <img>/<iframe> previews
    assert client.get("/api/jobs?api_key=secret123").status_code == 200
    # health stays public
    assert client.get("/api/health").status_code == 200
