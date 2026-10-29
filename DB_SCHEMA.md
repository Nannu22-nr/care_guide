# Database schema

SQLite (WAL mode, foreign keys on), file at `DB_PATH` (default `data/app.db`).
The schema is created by `db.init_db()` and is safe to run repeatedly (existing databases are upgraded in place).

```mermaid
erDiagram
    jobs ||--o{ corrections : "has"
    jobs ||--o{ processing_events : "has"
    jobs ||--o| invoice_index : "indexed as"
    jobs ||--o{ chunks : "split into"
    jobs ||--o| jobs_fts : "full-text"

    jobs {
        text id PK
        text filename
        text file_hash "SHA-256, exact-duplicate detection"
        text file_path
        int  file_size
        text status "queued|processing|ok|needs_review|failed"
        text stage  "queued|extracting_text|llm_extraction|validating|done|error"
        text raw_text "extracted / OCR text"
        text extraction "JSON: structured result + confidence"
        text validation "JSON: errors[], flags[], needs_review"
        text error
        real processing_time_sec
        real created_at
        real updated_at
    }
    corrections {
        int  id PK
        text job_id FK
        text field
        text old_value "JSON"
        text new_value "JSON"
        real corrected_at
    }
    processing_events {
        int  id PK
        text job_id FK
        text stage
        text status
        text message
        real created_at
    }
    invoice_index {
        text job_id PK
        text supplier_norm
        text invoice_no_norm
        real total
        text invoice_date
    }
    chunks {
        int  id PK
        text job_id FK
        int  chunk_index
        text text
        blob embedding "float32, L2-normalised"
    }
    jobs_fts {
        text job_id
        text filename
        text raw_text
        text extraction_text
    }
```

## Tables

| Table | Purpose | Notes |
|---|---|---|
| `jobs` | One row per uploaded document: metadata, extracted text, structured result, validation errors | indexes on `status`, `created_at`, `file_hash` |
| `processing_events` | Processing history: every stage transition, failure, reprocess, correction | index on `(job_id, id)` |
| `corrections` | Audit trail of manual edits (old/new value, timestamp) | cascade-deleted with the job |
| `invoice_index` | Normalised `(supplier, invoice_no)` for logical-duplicate detection | index on the pair |
| `jobs_fts` | FTS5 virtual table (filename, raw text, extracted values) for keyword search | rebuilt on completion and after each correction |
| `chunks` | ~800-char text chunks with stored embeddings for semantic search | loaded into memory at startup |

Line items live inside `jobs.extraction` (JSON). A relational `line_items` table was deliberately not added
yet: nothing queries line items individually.
