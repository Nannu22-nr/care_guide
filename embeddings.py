"""Semantic search with sentence-transformers + a numpy cosine index.

Design notes
------------
* Embeddings are stored in the `chunks.embedding` column (float32 bytes), so
  the in-memory index can be rebuilt after a restart WITHOUT re-running the
  model. Previously every new document re-embedded every chunk (O(n^2)) and the
  index was empty after a restart.
* Vectors are L2-normalised, so cosine similarity == dot product. For the
  data sizes of this project (<= ~1e5 chunks) a brute-force numpy matrix
  product is fast and removes the faiss dependency.
* sentence-transformers is optional; if it is missing, semantic search is
  disabled and the API falls back to keyword search.
"""
import threading

import numpy as np

from config import ENABLE_SEMANTIC_SEARCH, EMBEDDING_MODEL
from db import (
    get_all_chunks, save_chunks, set_chunk_embedding, get_chunk_jobs,
)

_lock = threading.Lock()
_model = None
_matrix: np.ndarray | None = None      # shape (n_chunks, dim), float32, normalised
_chunk_ids: list[int] = []

try:
    from sentence_transformers import SentenceTransformer  # noqa
    _HAS_ST = True
except ImportError:
    _HAS_ST = False

ENABLE_SEMANTIC_SEARCH = ENABLE_SEMANTIC_SEARCH and _HAS_ST


def _get_model():
    global _model
    if _model is None:
        if not _HAS_ST:
            raise RuntimeError("sentence-transformers not installed")
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks = []
    step = size - overlap
    for i in range(0, len(text), step):
        chunk = text[i:i + size]
        if chunk.strip():
            chunks.append(chunk)
        if i + size >= len(text):
            break
    return chunks


def embed_texts(texts: list[str]) -> np.ndarray:
    model = _get_model()
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False,
                        batch_size=32)
    return np.asarray(vecs, dtype="float32")


def _enabled() -> bool:
    return ENABLE_SEMANTIC_SEARCH


def load_index():
    """(Re)build the in-memory matrix from stored embeddings.

    Chunks that have no stored embedding yet (e.g. created by an older
    version) are embedded once and persisted.
    """
    global _matrix, _chunk_ids
    if not _enabled():
        return
    with _lock:
        rows = get_all_chunks()
        missing = [r for r in rows if not r["embedding"]]
        if missing:
            vecs = embed_texts([r["text"] for r in missing])
            for r, v in zip(missing, vecs):
                blob = v.astype("float32").tobytes()
                set_chunk_embedding(r["id"], blob)
                r["embedding"] = blob
        if not rows:
            _matrix, _chunk_ids = None, []
            return
        _matrix = np.vstack([np.frombuffer(r["embedding"], dtype="float32") for r in rows])
        _chunk_ids = [r["id"] for r in rows]


def index_job_text(job_id: str, raw_text: str):
    """Chunk + embed ONE document, persist it, refresh the index."""
    if not _enabled():
        return
    chunks = chunk_text(raw_text)
    if not chunks:
        save_chunks(job_id, [])
        load_index()
        return
    vecs = embed_texts(chunks)                      # only this document is embedded
    save_chunks(job_id, chunks, [v.astype("float32").tobytes() for v in vecs])
    load_index()                                    # cheap: no model inference


def semantic_search(query: str, top_k: int = 20) -> list[str]:
    """Return job ids ordered by best-matching chunk similarity."""
    if not _enabled():
        return []
    with _lock:
        if _matrix is None or len(_chunk_ids) == 0:
            return []
        qvec = embed_texts([query])[0]
        scores = _matrix @ qvec
        k = min(top_k * 3, len(scores))             # over-fetch: many chunks per job
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        ordered_chunk_ids = [_chunk_ids[i] for i in top]

    id_to_job = get_chunk_jobs(ordered_chunk_ids)
    ordered, seen = [], set()
    for cid in ordered_chunk_ids:
        jid = id_to_job.get(cid)
        if jid and jid not in seen:
            seen.add(jid)
            ordered.append(jid)
        if len(ordered) >= top_k:
            break
    return ordered
