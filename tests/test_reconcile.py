from reconcile import reconcile_documents


def test_matching_pair():
    docs = [
        {"job_id": "a", "filename": "a.pdf", "extraction": {
            "invoice_number": "INV-1", "supplier_name": "Acme", "total": 100.0,
            "subtotal": 90.0, "tax": 10.0, "currency": "USD", "invoice_date": "2024-01-01",
            "customer_name": "Beta"}},
        {"job_id": "b", "filename": "b.xlsx", "extraction": {
            "invoice_number": "INV-1", "supplier_name": "Acme", "total": 100.0,
            "subtotal": 90.0, "tax": 10.0, "currency": "USD", "invoice_date": "2024-01-01",
            "customer_name": "Beta"}},
    ]
    r = reconcile_documents(docs)
    assert r["summary"]["matched_invoices"] == 1
    assert r["summary"]["mismatches"] == 0
    assert r["pairs"][0]["match"] is True


def test_mismatched_total():
    docs = [
        {"job_id": "a", "filename": "a.pdf", "extraction": {
            "invoice_number": "INV-2", "supplier_name": "X", "total": 100.0}},
        {"job_id": "b", "filename": "b.xlsx", "extraction": {
            "invoice_number": "INV-2", "supplier_name": "X", "total": 150.0}},
    ]
    r = reconcile_documents(docs)
    assert r["summary"]["mismatches"] == 1
    assert r["pairs"][0]["match"] is False