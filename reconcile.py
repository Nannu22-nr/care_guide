"""Cross-verification: PDF↔PDF and PDF↔Excel/CSV."""
from collections import defaultdict


def _norm(s):
    return (str(s) if s is not None else "").strip().lower()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def reconcile_documents(jobs: list[dict], tolerance: float = 0.02) -> dict:
    """
    jobs: list of dicts, each with {job_id, filename, extraction}
    Returns structured reconciliation report.
    """
    report = {"pairs": [], "summary": {
        "total_docs": len(jobs),
        "matched_invoices": 0,
        "mismatches": 0,
        "unmatched": 0,
    }}

    # Group by normalized invoice number
    by_invoice: dict[str, list[dict]] = defaultdict(list)
    no_number = []
    for j in jobs:
        ext = j.get("extraction") or {}
        inv = _norm(ext.get("invoice_number"))
        if inv:
            by_invoice[inv].append(j)
        else:
            no_number.append(j)

    for inv_no, group in by_invoice.items():
        if len(group) < 2:
            report["summary"]["unmatched"] += 1
            continue
        report["summary"]["matched_invoices"] += 1
        # Compare every pair
        for i in range(len(group)):
            for k in range(i + 1, len(group)):
                a, b = group[i], group[k]
                pair = _compare_pair(a, b, tolerance)
                report["pairs"].append(pair)
                if not pair["match"]:
                    report["summary"]["mismatches"] += 1

    report["unmatched_docs"] = [j["filename"] for j in no_number]
    return report


def _compare_pair(a: dict, b: dict, tol: float) -> dict:
    ea, eb = a.get("extraction") or {}, b.get("extraction") or {}
    diffs = []

    for field in ["supplier_name", "customer_name", "currency", "invoice_date"]:
        va, vb = ea.get(field), eb.get(field)
        if _norm(va) != _norm(vb):
            diffs.append({"field": field, "a": va, "b": vb})

    for field in ["subtotal", "tax", "total"]:
        fa, fb = _num(ea.get(field)), _num(eb.get(field))
        if fa is not None and fb is not None and abs(fa - fb) > tol:
            diffs.append({"field": field, "a": fa, "b": fb, "delta": round(fa - fb, 2)})

    return {
        "job_a": a["job_id"],
        "file_a": a["filename"],
        "job_b": b["job_id"],
        "file_b": b["filename"],
        "invoice_number": ea.get("invoice_number"),
        "match": len(diffs) == 0,
        "diffs": diffs,
    }