# Evaluation Report

**Status: not yet run.** This file is overwritten automatically with measured results when you run:

```bash
python evaluation/make_dataset.py          # generates 26 labelled synthetic documents (deterministic)
export GROQ_API_KEY=...                    # evaluation uses the live LLM
python evaluation/run_eval.py --sleep 2    # writes evaluation/results.json and this report
```

Do not type numbers into this file by hand - the harness generates the tables (per-field accuracy,
invented-value count, document success/failure/review rates, p50/p95 time per file type, and the list of errors).

## What is already measured without the LLM (text-extraction layer only)

On the 26-document dataset, the OCR/PDF/spreadsheet layer produced text containing both the correct invoice
number and the correct total for **25 of 26** documents (the miss is a heavily degraded scanned PDF).
This is *not* the end-to-end accuracy; it only shows the input the LLM receives. End-to-end numbers come from `run_eval.py`.
