import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "run_eval", Path(__file__).resolve().parent.parent / "evaluation" / "run_eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

TRUTH = {"invoice_number": "INV-1", "invoice_date": "2024-05-01", "supplier_name": "Acme Corp",
         "customer_name": None, "subtotal": 100.0, "tax": None, "total": 100.0, "currency": "USD",
         "line_items": [{"amount": 100.0}]}


def test_perfect_prediction():
    s = ev.score_document(dict(TRUTH), TRUTH)
    assert all(v["correct"] for v in s.values())


def test_invented_value_is_wrong_and_counted():
    pred = dict(TRUTH, customer_name="Made Up Ltd")
    s = ev.score_document(pred, TRUTH)
    assert s["customer_name"]["correct"] is False and s["customer_name"]["invented"] is True


def test_missed_value_counted():
    pred = dict(TRUTH, total=None)
    s = ev.score_document(pred, TRUTH)
    assert s["total"]["missed"] is True and not s["total"]["correct"]


def test_normalisation():
    pred = dict(TRUTH, invoice_number="inv 1", invoice_date="01/05/2024",
                supplier_name="  ACME   corp ", total="100.004")
    s = ev.score_document(pred, TRUTH)
    assert all(v["correct"] for v in s.values()), {k: v for k, v in s.items() if not v["correct"]}


def test_percentile():
    assert ev.percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert ev.percentile([], 0.95) == 0.0


def test_summary_counts():
    good = {"file": "a.pdf", "type": "pdf", "status": "ok", "seconds": 1.0,
            "fields": ev.score_document(dict(TRUTH), TRUTH)}
    bad = {"file": "b.pdf", "type": "pdf", "status": "ok", "seconds": 3.0,
           "fields": ev.score_document(dict(TRUTH, total=5.0), TRUTH)}
    fail = {"file": "c.csv", "type": "csv", "status": "failed", "seconds": 0.1, "fields": None}
    s = ev.summarize([good, bad, fail])
    assert s["documents"] == 3
    assert s["document_failure_rate"] == round(1 / 3, 4)
    assert s["document_exact_match"]["correct"] == 1
    assert s["review_quality"]["silent_errors"] == 1     # bad was "ok" but wrong
    assert s["by_file_type"]["pdf"]["documents"] == 2
