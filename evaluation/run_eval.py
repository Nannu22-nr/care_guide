
import argparse
import json
import math
import os
import sys
import time
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Isolated DB + uploads for evaluation (must be set BEFORE importing project modules)
_EVAL_DIR = ROOT / "data" / "eval"
os.environ["DB_PATH"] = str(_EVAL_DIR / "eval.db")
os.environ["UPLOAD_DIR"] = str(_EVAL_DIR / "uploads")
os.environ["INDEX_DIR"] = str(_EVAL_DIR / "index")
os.environ["ENABLE_SEMANTIC_SEARCH"] = "false"

from validation import parse_date, to_decimal            # noqa: E402

DATASET = Path(__file__).parent / "dataset"
GT_PATH = DATASET / "ground_truth.json"
SCALAR_FIELDS = ["invoice_number", "invoice_date", "supplier_name", "customer_name",
                 "subtotal", "tax", "total", "currency"]
ALL_FIELDS = SCALAR_FIELDS + ["line_items"]
AMOUNT_TOL = 0.01


# --------------------------------------------------------------- scoring
def _norm_str(v):
    return " ".join(str(v).split()).casefold() if v not in (None, "") else None


def _is_null(v):
    return v is None or (isinstance(v, str) and not v.strip()) or v == []


def field_correct(field, pred, truth) -> bool:
    """Truth null  -> prediction must be null (an invented value is wrong).
    Truth present -> prediction must match after light normalisation."""
    if _is_null(truth):
        return _is_null(pred)
    if _is_null(pred):
        return False
    if field in ("subtotal", "tax", "total"):
        p, t = to_decimal(pred), to_decimal(truth)
        return p is not None and t is not None and abs(float(p) - float(t)) <= AMOUNT_TOL
    if field == "invoice_date":
        p, t = parse_date(pred), parse_date(truth)
        return p is not None and p == t
    if field == "currency":
        return _norm_str(pred) == _norm_str(truth)
    if field == "invoice_number":
        strip = lambda s: "".join(ch for ch in str(s).casefold() if ch.isalnum())
        return strip(pred) == strip(truth)
    return _norm_str(pred) == _norm_str(truth)


def line_items_correct(pred, truth) -> bool:
    p = [it for it in (pred or []) if isinstance(it, dict)]
    t = truth or []
    if len(p) != len(t):
        return False
    def amt(items):
        return sum(float(to_decimal(i.get("amount")) or 0) for i in items)
    return abs(amt(p) - amt(t)) <= AMOUNT_TOL * max(1, len(t))


def score_document(pred: dict, truth: dict) -> dict:
    """Per-field correctness for one document + hallucination flags."""
    pred = pred or {}
    res = {}
    for f in SCALAR_FIELDS:
        p, t = pred.get(f), truth.get(f)
        res[f] = {
            "correct": field_correct(f, p, t),
            "pred": p, "truth": t,
            "invented": _is_null(t) and not _is_null(p),   # value given though doc has none
            "missed": (not _is_null(t)) and _is_null(p),
        }
    res["line_items"] = {
        "correct": line_items_correct(pred.get("line_items"), truth.get("line_items")),
        "pred": len(pred.get("line_items") or []), "truth": len(truth.get("line_items") or []),
        "invented": False, "missed": False,
    }
    return res


def percentile(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(per_doc: list[dict]) -> dict:
    """per_doc items: {file, type, status, seconds, fields:{...}|None}"""
    total = len(per_doc)
    failed = [d for d in per_doc if d["status"] == "failed"]
    reviewed = [d for d in per_doc if d["status"] == "needs_review"]
    ok = [d for d in per_doc if d["status"] == "ok"]
    scored = [d for d in per_doc if d["fields"]]

    field_acc = {}
    for f in ALL_FIELDS:
        n = len(scored)
        c = sum(1 for d in scored if d["fields"][f]["correct"])
        field_acc[f] = {"correct": c, "total": n, "accuracy": round(c / n, 4) if n else 0.0}
    n_null_truth = sum(1 for d in scored for f in SCALAR_FIELDS if _is_null(d["fields"][f]["truth"]))
    invented = sum(1 for d in scored for f in SCALAR_FIELDS if d["fields"][f]["invented"])
    missed = sum(1 for d in scored for f in SCALAR_FIELDS if d["fields"][f]["missed"])
    n_present_truth = sum(1 for d in scored for f in SCALAR_FIELDS if not _is_null(d["fields"][f]["truth"]))
    cells = len(scored) * len(SCALAR_FIELDS)
    cell_correct = sum(1 for d in scored for f in SCALAR_FIELDS if d["fields"][f]["correct"])
    doc_exact = sum(1 for d in scored if all(d["fields"][f]["correct"] for f in ALL_FIELDS))

    # a "false review" = every field right but the system still asked for review
    false_review = sum(1 for d in reviewed if d["fields"] and all(d["fields"][f]["correct"] for f in ALL_FIELDS))
    # a "missed error" = system said ok although a field was wrong
    silent_error = sum(1 for d in ok if d["fields"] and not all(d["fields"][f]["correct"] for f in ALL_FIELDS))

    times = [d["seconds"] for d in per_doc]
    by_type = {}
    for d in per_doc:
        by_type.setdefault(d["type"], []).append(d)
    type_stats = {}
    for t, docs in sorted(by_type.items()):
        ts = [x["seconds"] for x in docs]
        sc = [x for x in docs if x["fields"]]
        type_stats[t] = {
            "documents": len(docs),
            "failed": sum(1 for x in docs if x["status"] == "failed"),
            "p50_sec": round(percentile(ts, 0.5), 2),
            "p95_sec": round(percentile(ts, 0.95), 2),
            "doc_exact_match": sum(1 for x in sc if all(x["fields"][f]["correct"] for f in ALL_FIELDS)),
            "field_accuracy": round(
                sum(1 for x in sc for f in SCALAR_FIELDS if x["fields"][f]["correct"]) /
                max(1, len(sc) * len(SCALAR_FIELDS)), 4),
        }

    return {
        "documents": total,
        "document_success_rate": round((len(ok) + len(reviewed)) / total, 4) if total else 0,
        "document_failure_rate": round(len(failed) / total, 4) if total else 0,
        "needs_review_rate": round(len(reviewed) / total, 4) if total else 0,
        "clean_ok_rate": round(len(ok) / total, 4) if total else 0,
        "overall_field_accuracy": round(cell_correct / cells, 4) if cells else 0,
        "document_exact_match": {"correct": doc_exact, "total": len(scored),
                                 "rate": round(doc_exact / len(scored), 4) if scored else 0},
        "field_accuracy": field_acc,
        "null_handling": {
            "fields_that_should_be_null": n_null_truth,
            "invented_values": invented,
            "fields_with_value_but_missed": missed,
            "fields_with_value_total": n_present_truth,
        },
        "review_quality": {"false_reviews": false_review, "silent_errors": silent_error},
        "time_sec": {"mean": round(sum(times) / len(times), 2) if times else 0,
                     "p50": round(percentile(times, 0.5), 2),
                     "p95": round(percentile(times, 0.95), 2),
                     "max": round(max(times), 2) if times else 0},
        "by_file_type": type_stats,
    }


# ----------------------------------------------------------------- report
def render_report(summary: dict, meta: dict, per_doc: list[dict]) -> str:
    s = summary
    L = ["# Evaluation Report", "",
         f"_Generated by `evaluation/run_eval.py` on {meta['date']} - all numbers are measured, none estimated._", "",
         "## Setup", "",
         f"- Dataset: **{s['documents']} labelled synthetic documents** (`evaluation/make_dataset.py`, seed 42) - "
         f"digital PDFs, scanned PDFs, clean + noisy PNGs, XLSX (one multi-sheet), CSV; includes 2 hard cases whose correct answer is `null` (no tax / no customer printed).",
         f"- Pipeline: PyMuPDF/Tesseract OCR -> LLM `{meta['model']}` (Groq) -> validation rules.",
         f"- Prompt version: `{meta['prompt_version']}`. Evaluation uses live LLM calls.",
         "- Matching: amounts +-0.01, dates compared as parsed dates, names case/whitespace-insensitive, "
         "truth `null` => prediction must be `null` (an invented value counts as wrong).", "",
         "## Headline results", "",
         "| Metric | Value |", "|---|---|",
         f"| Document success rate (pipeline finished) | {s['document_success_rate']:.1%} |",
         f"| Document failure rate | {s['document_failure_rate']:.1%} |",
         f"| Flagged `needs_review` | {s['needs_review_rate']:.1%} |",
         f"| Overall field accuracy (8 scalar fields) | {s['overall_field_accuracy']:.1%} |",
         f"| Documents with every field correct | {s['document_exact_match']['correct']}/{s['document_exact_match']['total']} ({s['document_exact_match']['rate']:.1%}) |",
         f"| Processing time p50 / p95 / max | {s['time_sec']['p50']}s / {s['time_sec']['p95']}s / {s['time_sec']['max']}s |", "",
         "## Per-field accuracy", "", "| Field | Correct | Accuracy |", "|---|---|---|"]
    for f, v in s["field_accuracy"].items():
        L.append(f"| {f} | {v['correct']}/{v['total']} | {v['accuracy']:.1%} |")
    nh = s["null_handling"]; rq = s["review_quality"]
    L += ["", "## Missing-value handling & review quality", "",
          f"- Fields whose true value is `null`: {nh['fields_that_should_be_null']} -> **invented values: {nh['invented_values']}**",
          f"- Fields with a real value that the system left empty: {nh['fields_with_value_but_missed']} of {nh['fields_with_value_total']}",
          f"- False reviews (all fields right, still flagged): {rq['false_reviews']}",
          f"- Silent errors (marked `ok` but a field was wrong): {rq['silent_errors']}", "",
          "## By file type", "",
          "| Type | Docs | Failed | Exact docs | Field acc. | p50 | p95 |", "|---|---|---|---|---|---|---|"]
    for t, v in s["by_file_type"].items():
        L.append(f"| {t} | {v['documents']} | {v['failed']} | {v['doc_exact_match']} | {v['field_accuracy']:.1%} | {v['p50_sec']}s | {v['p95_sec']}s |")
    errs = [(d["file"], f, d["fields"][f]) for d in per_doc if d["fields"] for f in ALL_FIELDS if not d["fields"][f]["correct"]]
    L += ["", "## Errors", ""]
    if not errs:
        L.append("No field errors in this run.")
    else:
        L += ["| File | Field | Predicted | Truth |", "|---|---|---|---|"]
        for fn, f, v in errs[:60]:
            L.append(f"| {fn} | {f} | `{v['pred']}` | `{v['truth']}` |")
    failed = [d for d in per_doc if d["status"] == "failed"]
    if failed:
        L += ["", "## Failed documents", ""] + [f"- `{d['file']}`: {d.get('error')}" for d in failed]
    L += ["", "## Limitations of this evaluation", "",
          "- Synthetic documents have clean, regular layouts; real-world invoices (stamps, tables spanning pages, handwriting) will score lower.",
          "- Single run, small N: treat differences of a few points as noise.",
          "- Timing was measured on one machine and includes network latency to the LLM provider.",
          "- Multi-invoice PDFs (one file, many invoices) are not covered by this dataset.", ""]
    return "\n".join(L)


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds to wait between documents (rate limits)")
    ap.add_argument("--limit", type=int, default=0, help="evaluate only the first N documents")
    args = ap.parse_args()

    import shutil
    shutil.rmtree(_EVAL_DIR, ignore_errors=True)      # always start from a clean DB
    _EVAL_DIR.mkdir(parents=True, exist_ok=True)

    from config import GROQ_API_KEY, GROQ_MODEL
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY is not set - the evaluation needs the live LLM. Aborting.")
    if not GT_PATH.exists():
        sys.exit("Missing dataset. Run: python evaluation/make_dataset.py")

    from db import init_db, create_job, get_job
    from pipeline import process_document
    from llm import PROMPT_VERSION
    init_db()

    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))
    items = list(gt.items())[: args.limit or None]
    per_doc = []
    for n, (fname, truth) in enumerate(items, 1):
        fpath = DATASET / fname
        if not fpath.exists():
            print(f"[skip] {fname} missing")
            continue
        data = fpath.read_bytes()
        job_id = f"eval_{n:03d}"
        create_job(job_id, fname, hashlib.sha256(data).hexdigest(), str(fpath), len(data))
        t0 = time.time()
        try:
            res = process_document(data, fname, job_id)
        except Exception as e:                       # a crash counts as a failure, never hidden
            res = {"status": "failed", "error": f"crash: {e}"}
        seconds = time.time() - t0

        job = get_job(job_id) or {}
        pred = job.get("extraction")
        pred = json.loads(pred) if isinstance(pred, str) and pred else {}
        fields = score_document(pred, truth) if res.get("status") != "failed" else None
        per_doc.append({"file": fname, "type": fname.rsplit(".", 1)[-1].lower(),
                        "status": res.get("status"), "seconds": round(seconds, 2),
                        "error": res.get("error"), "fields": fields})
        ok = fields and all(v["correct"] for v in fields.values())
        print(f"[{n:02d}/{len(items)}] {'EXACT  ' if ok else res.get('status','?').upper():8s} {fname}  {seconds:.1f}s")
        if args.sleep:
            time.sleep(args.sleep)

    summary = summarize(per_doc)
    meta = {"date": time.strftime("%Y-%m-%d"), "model": GROQ_MODEL, "prompt_version": PROMPT_VERSION}
    out = Path(__file__).parent
    (out / "results.json").write_text(json.dumps({"meta": meta, "summary": summary, "per_doc": per_doc},
                                                  indent=2, default=str), encoding="utf-8")
    (out / "REPORT.md").write_text(render_report(summary, meta, per_doc), encoding="utf-8")
    print("\nSaved evaluation/results.json and evaluation/REPORT.md")
    print(json.dumps({k: summary[k] for k in ("documents", "document_success_rate", "document_failure_rate",
                                              "overall_field_accuracy", "time_sec")}, indent=2))


if __name__ == "__main__":
    main()
