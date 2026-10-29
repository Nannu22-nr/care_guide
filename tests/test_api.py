import json
import time

from fastapi.testclient import TestClient

from app import app
from db import create_job, update_job, get_job, index_for_fts, upsert_invoice_index

client = TestClient(app)


def _seed_job(unique, extraction=None, status="needs_review", filename=None):
    job_id = f"job_{unique}"
    create_job(job_id, filename or f"{unique}.csv", f"hash_{unique}", "/tmp/none", 10)
    ext = extraction or {
        "invoice_number": f"INV-{unique}", "invoice_date": "2024-05-01",
        "supplier_name": "Acme Corp", "customer_name": "Beta", "subtotal": 100.0,
        "tax": 10.0, "total": 110.0, "currency": "USD",
        "line_items": [{"description": "X", "quantity": 1, "unit_price": 100.0, "amount": 100.0}],
        "confidence": {"invoice_number": 0.9, "total": 0.9, "supplier_name": 0.9},
    }
    update_job(job_id, status=status, stage="done", raw_text=f"raw text {unique} acme",
               extraction=json.dumps(ext))
    index_for_fts(job_id, f"{unique}.csv", f"raw text {unique} acme", json.dumps(ext))
    upsert_invoice_index(job_id, ext["supplier_name"], ext["invoice_number"],
                         ext["total"], ext["invoice_date"])
    return job_id


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert "status" in r.json()


def test_config_endpoint():
    assert "auth_required" in client.get("/api/config").json()


def test_upload_csv_creates_queued_job(unique):
    csv = f"invoice_number,supplier,total\nINV-{unique},ACME,100\n".encode()
    r = client.post("/api/upload", files={"files": ("t.csv", csv, "text/csv")})
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["status"] == "queued"
    assert get_job(res["job_id"])["status"] in ("queued", "processing", "ok", "failed", "needs_review")


def test_upload_multiple_files(unique):
    files = [("files", (f"m{i}.csv", f"a,b\n{unique},{i}\n".encode(), "text/csv")) for i in range(3)]
    r = client.post("/api/upload", files=files)
    assert len(r.json()["results"]) == 3


def test_get_nonexistent_job():
    assert client.get("/api/jobs/doesnotexist").status_code == 404


def test_job_detail_has_events_and_corrections(unique):
    jid = _seed_job(unique)
    data = client.get(f"/api/jobs/{jid}").json()
    assert data["id"] == jid
    assert isinstance(data["events"], list) and data["events"]
    assert data["corrections"] == []
    assert "raw_text" not in data and "file_path" not in data


def test_list_jobs_pagination():
    r = client.get("/api/jobs?limit=2&offset=0").json()
    assert len(r["jobs"]) <= 2 and r["limit"] == 2


def test_stats():
    data = client.get("/api/stats").json()
    for k in ("total_jobs", "success_rate", "failure_rate", "review_rate"):
        assert k in data


# --------------------------------------------------------- corrections
def test_correction_revalidates_and_logs(unique):
    jid = _seed_job(unique, status="needs_review")
    # break the total, then fix it again through the API
    r = client.post(f"/api/jobs/{jid}/correct", json={"field": "total", "value": "999"})
    assert r.status_code == 200
    assert r.json()["status"] == "needs_review"
    assert any(e["rule"] == "TOTAL_MISMATCH" for e in r.json()["validation"]["errors"])

    r = client.post(f"/api/jobs/{jid}/correct", json={"field": "total", "value": "110"})
    assert r.json()["status"] == "ok"

    hist = client.get(f"/api/jobs/{jid}").json()["corrections"]
    assert [c["field"] for c in hist] == ["total", "total"]


def test_correction_rejects_unknown_field_and_non_numeric(unique):
    jid = _seed_job(unique)
    assert client.post(f"/api/jobs/{jid}/correct", json={"field": "hacker", "value": "1"}).status_code == 400
    assert client.post(f"/api/jobs/{jid}/correct", json={"field": "total", "value": "abc"}).status_code == 400
    assert client.post("/api/jobs/nope/correct", json={"field": "total", "value": "1"}).status_code == 404


def test_correction_keeps_duplicate_detection(unique):
    a = _seed_job(unique + "a")
    b = _seed_job(unique + "b")
    # make b's invoice number equal to a's -> must be flagged as duplicate
    inv_a = json.loads(get_job(a)["extraction"])["invoice_number"]
    r = client.post(f"/api/jobs/{b}/correct", json={"field": "invoice_number", "value": inv_a})
    assert any(e["rule"] == "DUPLICATE_INVOICE" for e in r.json()["validation"]["errors"])


# ----------------------------------------------------------- reprocess
def test_reprocess_failed_job(unique):
    jid = _seed_job(unique, status="failed")
    r = client.post(f"/api/jobs/{jid}/reprocess")
    assert r.status_code == 200 and r.json()["status"] == "queued"


def test_reprocess_rejected_while_running(unique):
    jid = _seed_job(unique, status="processing")
    assert client.post(f"/api/jobs/{jid}/reprocess").status_code == 409


def test_reprocess_failed_bulk(unique):
    _seed_job(unique, status="failed")
    r = client.post("/api/reprocess-failed")
    assert r.status_code == 200 and r.json()["requeued"] >= 1


# --------------------------------------------------------------- search
def test_keyword_search_and_filters(unique):
    jid = _seed_job(unique, status="needs_review")
    r = client.get(f"/api/search?q={unique}").json()
    assert jid in [j["id"] for j in r["jobs"]]

    r = client.get(f"/api/search?q={unique}&status=ok").json()
    assert jid not in [j["id"] for j in r["jobs"]]

    r = client.get(f"/api/search?supplier=acme&status=needs_review").json()
    assert jid in [j["id"] for j in r["jobs"]]


def test_empty_search_returns_nothing():
    assert client.get("/api/search?q=").json()["jobs"] == []


def test_semantic_falls_back_to_keyword_when_disabled(unique):
    _seed_job(unique)
    r = client.get(f"/api/search?q={unique}&mode=semantic").json()
    assert r["mode"] == "keyword" and r["jobs"]


# ------------------------------------------------------------ reconcile
def test_reconcile_endpoint(unique):
    a = _seed_job(unique + "r1")
    b = _seed_job(unique + "r2")
    # same invoice number, different totals
    ext_b = json.loads(get_job(b)["extraction"])
    ext_a = json.loads(get_job(a)["extraction"])
    ext_b["invoice_number"] = ext_a["invoice_number"]
    ext_b["total"] = 777.0
    update_job(b, extraction=json.dumps(ext_b))
    r = client.post("/api/reconcile", json={"job_ids": [a, b]}).json()
    assert r["summary"]["mismatches"] == 1


def test_delete_job(unique):
    jid = _seed_job(unique)
    assert client.delete(f"/api/jobs/{jid}").status_code == 200
    assert client.get(f"/api/jobs/{jid}").status_code == 404
