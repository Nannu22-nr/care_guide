import sqlite3
import json
import time
import os
from contextlib import contextmanager
from config import DB_PATH


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER,
            status TEXT NOT NULL DEFAULT 'queued',
            stage TEXT DEFAULT 'queued',
            raw_text TEXT,
            extraction TEXT,
            validation TEXT,
            error TEXT,
            processing_time_sec REAL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_jobs_hash ON jobs(file_hash);

        CREATE TABLE IF NOT EXISTS corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            corrected_at REAL NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_corr_job ON corrections(job_id);

        -- Processing history: one row per stage transition / notable event
        CREATE TABLE IF NOT EXISTS processing_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT,
            message TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_events_job ON processing_events(job_id, id);

        CREATE TABLE IF NOT EXISTS invoice_index (
            job_id TEXT PRIMARY KEY,
            supplier_norm TEXT,
            invoice_no_norm TEXT,
            total REAL,
            invoice_date TEXT,
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_inv_supplier ON invoice_index(supplier_norm);
        CREATE INDEX IF NOT EXISTS idx_inv_no ON invoice_index(invoice_no_norm);
        CREATE INDEX IF NOT EXISTS idx_inv_pair ON invoice_index(supplier_norm, invoice_no_norm);

        CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
            job_id UNINDEXED, filename, raw_text, extraction_text
        );

        -- Text chunks + their embedding (float32 bytes) for semantic search
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding BLOB,
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_job ON chunks(job_id);
        """)


# ------------------------------------------------------------------ jobs
def create_job(job_id, filename, file_hash, file_path, file_size):
    now = time.time()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO jobs (id, filename, file_hash, file_path, file_size,
                                 status, stage, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'queued', 'queued', ?, ?)""",
            (job_id, filename, file_hash, file_path, file_size, now, now),
        )
        conn.execute(
            "INSERT INTO processing_events (job_id, stage, status, message, created_at) "
            "VALUES (?, 'queued', 'queued', 'File accepted', ?)",
            (job_id, now),
        )


def find_job_by_hash(file_hash: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE file_hash = ? ORDER BY created_at LIMIT 1",
            (file_hash,),
        ).fetchone()
        return dict(row) if row else None


def update_job(job_id, **fields):
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [job_id]
    with get_db() as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", vals)


def get_job(job_id, include_raw=False):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        if not include_raw:
            out.pop("raw_text", None)
            out.pop("file_path", None)
        return out


def delete_job(job_id):
    """Deletes a job, its physical file, and all related DB records."""
    with get_db() as conn:
        row = conn.execute("SELECT file_path FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row and row["file_path"]:
            path = row["file_path"]
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"Failed to delete file {path}: {e}")

        conn.execute("DELETE FROM corrections WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM processing_events WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM invoice_index WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM chunks WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs_fts WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def list_jobs(limit=100, offset=0, status=None):
    with get_db() as conn:
        base = ("SELECT id, filename, status, stage, processing_time_sec, created_at "
                "FROM jobs")
        if status:
            rows = conn.execute(
                base + " WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                base + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]


def count_jobs(status=None) -> int:
    with get_db() as conn:
        if status:
            r = conn.execute("SELECT COUNT(*) c FROM jobs WHERE status = ?", (status,)).fetchone()
        else:
            r = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()
        return r["c"]


def get_stuck_jobs() -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status IN ('queued','processing')"
        ).fetchall()
        return [r["id"] for r in rows]


def get_stats() -> dict:
    """Aggregate metrics.

    - success_rate: finished without a pipeline failure (ok + needs_review)
    - clean_rate:   finished and passed validation (ok only)
    - review_rate / failure_rate: share of needs_review / failed jobs
    """
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        by_status = {
            r["status"]: r["c"]
            for r in conn.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()
        }
        avg_time = conn.execute(
            "SELECT AVG(processing_time_sec) a FROM jobs WHERE processing_time_sec > 0"
        ).fetchone()["a"] or 0
        ok = by_status.get("ok", 0)
        review = by_status.get("needs_review", 0)
        failed = by_status.get("failed", 0)

        def rate(n):
            return round(n / total, 4) if total else 0

        return {
            "total_jobs": total,
            "by_status": by_status,
            "avg_processing_time_sec": round(avg_time, 2),
            "success_rate": rate(ok + review),
            "clean_rate": rate(ok),
            "review_rate": rate(review),
            "failure_rate": rate(failed),
        }


# ---------------------------------------------------------------- events
def add_event(job_id: str, stage: str, status: str | None = None, message: str | None = None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO processing_events (job_id, stage, status, message, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, stage, status, (message or "")[:500], time.time()),
        )


def get_events(job_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT stage, status, message, created_at FROM processing_events "
            "WHERE job_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------------ FTS search
def _extraction_to_text(extraction_json: str | None) -> str:
    if not extraction_json:
        return ""
    try:
        ext = json.loads(extraction_json)
    except Exception:
        return ""
    parts = []
    for k, v in ext.items():
        if k == "confidence" or k.startswith("_"):
            continue
        if v is None:
            continue
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict):
                    parts.append(" ".join(str(x) for x in it.values() if x))
                else:
                    parts.append(str(it))
        else:
            parts.append(str(v))
    return " ".join(parts)


def index_for_fts(job_id: str, filename: str, raw_text: str, extraction_json: str | None):
    ext_text = _extraction_to_text(extraction_json)
    with get_db() as conn:
        conn.execute("DELETE FROM jobs_fts WHERE job_id = ?", (job_id,))
        conn.execute(
            "INSERT INTO jobs_fts (job_id, filename, raw_text, extraction_text) VALUES (?, ?, ?, ?)",
            (job_id, filename or "", raw_text or "", ext_text),
        )


def search_jobs_keyword(query: str, limit=50, offset=0, status=None, supplier=None):
    """Keyword search (FTS5, with LIKE fallback) plus optional filters.

    `query` may be empty when at least one filter is given.
    """
    q = (query or "").strip()
    supplier_like = f"%{supplier.strip().lower()}%" if supplier and supplier.strip() else None
    if not q and not status and not supplier_like:
        return []

    filt_sql, filt_args = "", []
    if status:
        filt_sql += " AND j.status = ?"
        filt_args.append(status)
    if supplier_like:
        filt_sql += (" AND EXISTS (SELECT 1 FROM invoice_index i "
                     "WHERE i.job_id = j.id AND i.supplier_norm LIKE ?)")
        filt_args.append(supplier_like)

    with get_db() as conn:
        rows = []
        if q:
            safe = q.replace('"', '""')
            try:
                rows = conn.execute(
                    f"""SELECT j.id, j.filename, j.status, j.created_at
                        FROM jobs_fts f JOIN jobs j ON j.id = f.job_id
                        WHERE jobs_fts MATCH ? {filt_sql}
                        ORDER BY rank LIMIT ? OFFSET ?""",
                    (f'"{safe}"*', *filt_args, limit, offset),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                like = f"%{q.lower()}%"
                rows = conn.execute(
                    f"""SELECT j.id, j.filename, j.status, j.created_at FROM jobs j
                        WHERE (LOWER(j.filename) LIKE ?
                           OR LOWER(COALESCE(j.raw_text,'')) LIKE ?
                           OR LOWER(COALESCE(j.extraction,'')) LIKE ?) {filt_sql}
                        ORDER BY j.created_at DESC LIMIT ? OFFSET ?""",
                    (like, like, like, *filt_args, limit, offset),
                ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT j.id, j.filename, j.status, j.created_at FROM jobs j
                    WHERE 1=1 {filt_sql}
                    ORDER BY j.created_at DESC LIMIT ? OFFSET ?""",
                (*filt_args, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]


def search_jobs_by_ids(job_ids: list[str], limit=50):
    if not job_ids:
        return []
    placeholders = ",".join("?" for _ in job_ids)
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT id, filename, status, created_at FROM jobs
                WHERE id IN ({placeholders}) ORDER BY created_at DESC LIMIT ?""",
            (*job_ids, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------------ corrections
def add_correction(job_id, field, old_value, new_value):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO corrections (job_id, field, old_value, new_value, corrected_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, field, json.dumps(old_value), json.dumps(new_value), time.time()),
        )


def get_corrections(job_id):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM corrections WHERE job_id = ? ORDER BY corrected_at",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------- duplicate index
def upsert_invoice_index(job_id, supplier, invoice_no, total, invoice_date):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO invoice_index (job_id, supplier_norm, invoice_no_norm, total, invoice_date)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                 supplier_norm=excluded.supplier_norm,
                 invoice_no_norm=excluded.invoice_no_norm,
                 total=excluded.total,
                 invoice_date=excluded.invoice_date""",
            (
                job_id,
                (supplier or "").strip().lower(),
                str(invoice_no or "").strip().lower(),
                total,
                invoice_date,
            ),
        )


def find_duplicate_invoice(supplier: str, invoice_no: str, exclude_job_id: str) -> dict | None:
    s = (supplier or "").strip().lower()
    n = str(invoice_no or "").strip().lower()
    if not s or not n:
        return None
    with get_db() as conn:
        row = conn.execute(
            """SELECT j.id, j.filename FROM invoice_index i
               JOIN jobs j ON j.id = i.job_id
               WHERE i.supplier_norm = ? AND i.invoice_no_norm = ? AND i.job_id != ?
               LIMIT 1""",
            (s, n, exclude_job_id),
        ).fetchone()
        return dict(row) if row else None


# ----------------------------------------------------------------- chunks
def save_chunks(job_id: str, chunks: list[str], embeddings: list[bytes] | None = None):
    """Replace all chunks of a job. `embeddings` (float32 bytes) is optional."""
    with get_db() as conn:
        conn.execute("DELETE FROM chunks WHERE job_id = ?", (job_id,))
        for i, txt in enumerate(chunks):
            emb = embeddings[i] if embeddings else None
            conn.execute(
                "INSERT INTO chunks (job_id, chunk_index, text, embedding) VALUES (?, ?, ?, ?)",
                (job_id, i, txt, emb),
            )


def set_chunk_embedding(chunk_id: int, embedding: bytes):
    with get_db() as conn:
        conn.execute("UPDATE chunks SET embedding = ? WHERE id = ?", (embedding, chunk_id))


def get_all_chunks() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, job_id, chunk_index, text, embedding FROM chunks"
        ).fetchall()
        return [dict(r) for r in rows]


def get_chunk_jobs(chunk_ids: list[int]) -> dict[int, str]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, job_id FROM chunks WHERE id IN ({placeholders})", chunk_ids
        ).fetchall()
        return {r["id"]: r["job_id"] for r in rows}


def get_chunks_for_jobs(job_ids: list[str]) -> dict[str, list[str]]:
    if not job_ids:
        return {}
    placeholders = ",".join("?" for _ in job_ids)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT job_id, chunk_index, text FROM chunks WHERE job_id IN ({placeholders}) "
            f"ORDER BY job_id, chunk_index",
            tuple(job_ids),
        ).fetchall()
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["job_id"], []).append(r["text"])
        return out
