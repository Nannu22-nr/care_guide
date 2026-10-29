import threading
import queue
from pathlib import Path

_job_queue: "queue.Queue[str]" = queue.Queue()
_worker_started = False


def enqueue(job_id: str):
    _job_queue.put(job_id)


def _worker_loop():
    while True:
        job_id = _job_queue.get()
        if job_id is None:
            break
        try:
            from db import get_job, update_job
            from pipeline import process_document
            job = get_job(job_id, include_raw=True)
            if not job:
                continue
            path = Path(job["file_path"])
            if not path.exists():
                update_job(job_id, status="failed", stage="error",
                           error="Uploaded file missing on disk")
                continue
            data = path.read_bytes()
            process_document(data, job["filename"], job_id)
        except Exception as e:
            try:
                from db import update_job
                update_job(job_id, status="failed", stage="error", error=f"worker: {e}")
            except Exception:
                pass
        finally:
            _job_queue.task_done()


def start_workers(n=2):
    global _worker_started
    if _worker_started:
        return
    for _ in range(n):
        t = threading.Thread(target=_worker_loop, daemon=True)
        t.start()
    _worker_started = True


def requeue_stuck_jobs():
    """On startup, re-enqueue jobs left in queued/processing."""
    from db import get_stuck_jobs, update_job
    ids = get_stuck_jobs()
    for jid in ids:
        update_job(jid, status="queued", stage="queued")
        enqueue(jid)
    return len(ids)