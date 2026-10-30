# Document Intelligence

AI-powered invoice processing: upload PDF / JPG / PNG / TIFF / XLSX / XLS / CSV files, extract text (digital
text, OCR, spreadsheets), pull out structured invoice fields with an LLM, validate the numbers, flag records
for manual review, correct values in a web UI, search everything (keyword + semantic) and cross-verify
documents against each other.

* Architecture diagram: [ARCHITECTURE.md](ARCHITECTURE.md) · Database / ER diagram: [DB_SCHEMA.md](DB_SCHEMA.md)
* Prompts: [PROMPTS.md](PROMPTS.md) · AI disclosure: [AI_DISCLOSUREsearch, cross-verify |
| 7 Tests & evaluation | `tests/`, `evaluation/` |

## API

All endpoints except `/`, `/api/health` and `/api/config` need the API key when `API_KEY` is set
(`X-API-Key` header, or `?api_key=` for image/PDF previews).

| Method & path | Purpose |
|---|---|
| `POST /api/upload` | Upload one or many files (multipart `files`) |
| `GET /api/jobs?limit&offset&status` | List jobs / processing status |
| `GET /api/jobs/{id}` | Extracted result, validation errors, corrections, history |
| `GET /api/jobs/{id}/events` | Processing history only |
| `POST /api/jobs/{id}/correct` | Manual correction `{field, value}` (re-validates, audit-logged) |
| `POST /api/jobs/{id}/reprocess` | Re-queue a job (409 if already running) |
| `POST /api/reprocess-failed` | Re-queue every failed job |
| `DELETE /api/jobs/{id}` | Delete job, file and index entries |
| `GET /api/search?q&mode=keyword\|semantic&status&supplier` | Search |
| `POST /api/reconcile` | Cross-verify documents `{all:true}` or `{job_ids:[...]}` |
| `GET /api/files/{id}` / `/api/download/{id}` | Preview / download the original file |
| `GET /api/stats` · `/api/health` · `/api/config` | Metrics, health, UI config |

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Tests never call the real LLM (it is mocked) and use a temporary database. OCR tests are skipped
automatically if the `tesseract` binary is not installed.

## Evaluation

```bash
python evaluation/make_dataset.py        # 26 labelled synthetic documents, deterministic
python evaluation/run_eval.py --sleep 2  # live LLM; writes results.json + REPORT.md
```

`REPORT.md` is generated from the run (accuracy per field, invented-value count, success/failure/review
rates, p50/p95 time). It makes live LLM requests and may incur provider usage.

## Deploying on a small host (e.g. Render free tier, 512 MB RAM)

* Use the included `Dockerfile` (it installs Tesseract) and `requirements.txt` (no torch).
* Set environment variables: `GROQ_API_KEY`, `API_KEY`, `ENABLE_SEMANTIC_SEARCH=false`,
  `WORKER_THREADS=1`, `MAX_FILE_MB=50`.
* Free tiers have an ephemeral disk: uploaded files and the SQLite database are lost on
  restart/redeploy, and the service sleeps when idle (first request can take ~1 minute).

## Security

* Set `API_KEY` in `.env` for any deployment beyond local development. When it is empty, auth is **disabled**.
* CORS is same-origin by default; list extra origins in `ALLOWED_ORIGINS`.
* Uploads: extension allow-list, MIME sniffing, streamed size cap (`MAX_FILE_MB`), SHA-256 de-duplication,
  files stored under random ids (the original name is never used as a path), `nosniff` on downloads, rate limiting.
* The container runs as a non-root user.
* **Never commit `.env`, uploaded documents or the database** (`.gitignore` covers them). Rotate any key that was ever shared.
* Document text is sent to the LLM provider - see [AI_DISCLOSURE.md](AI_DISCLOSURE.md).

## Known limitations (honest list)

* **One invoice per file.** A single PDF/sheet that contains many invoices is treated as one document
  (no invoice-boundary segmentation yet). Large multi-invoice PDFs and one-row-per-invoice spreadsheets
  therefore need a splitting step.
* **Single-node queue.** Jobs run in an in-process thread pool backed by SQLite; it recovers stuck jobs on
  restart but does not scale across machines. Redis + a worker service (and PostgreSQL) is the next step.
* OCR of very noisy scans is imperfect (see the evaluation report for measured errors).
* Confidence values are self-reported by the LLM and are only a weak signal.
* Cross-verification matches documents by invoice number, which relies on that field being extracted correctly..md](AI_DISCLOSURE.md)
* Evaluation: [evaluation/REPORT.md](evaluation/REPORT.md)

## Requirements

* Python 3.10+
* Tesseract OCR on `PATH` (images / scanned PDFs)
* A Groq API key (LLM extraction)
* Optional: `sentence-transformers` for semantic search (large, needs ~1 GB RAM).
  It is only in `requirements-dev.txt`; if it is not installed, semantic search is
  disabled automatically and the API falls back to keyword search.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt   # runtime + tests + optional semantic search
Copy-Item .env.example .env           # then set GROQ_API_KEY (and API_KEY, see Security)
uvicorn app:app --reload
```

Open <http://127.0.0.1:8000>. Uploaded files and the SQLite database live under `data/`.

## Docker

```bash
cp .env.example .env     # set GROQ_API_KEY (and API_KEY)
docker compose up --build
```

## Features by assessment task

| Task | Where |
|---|---|
| 1 Document processing | `extraction.py` - per-page digital/OCR routing, adaptive OpenCV preprocessing, multi-page PDF, multi-frame TIFF, multi-sheet Excel, corrupted-file handling |
| 2 AI extraction | `llm.py` - JSON mode, Pydantic schema, `null` for unknown values, per-field confidence, retries, cache |
| 3 Validation | `validation.py` - missing fields, non-numeric/negative/zero amounts, subtotal + tax = total, sum(line items) = subtotal, per-item qty x price, tax plausibility, date sanity, duplicate file + duplicate invoice, low confidence. Money uses `Decimal` |
| 4 Backend API | `app.py` - see table below; background processing in `jobs.py` |
| 5 DB & search | `db.py`, `embeddings.py` - SQLite + FTS5 keyword search with status/supplier filters, semantic search (MiniLM), processing history table |
| 6 Frontend | `templates/index.html`, `static/script.js` - multi-upload, live progress, validation errors, history, inline correction, 