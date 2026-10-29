"""Semantic index logic, tested with a fake embedder (no model download)."""
import numpy as np

import embeddings
from db import create_job


def _fake_embed(texts):
    # deterministic bag-of-letters vectors; normalised
    out = []
    for t in texts:
        v = np.zeros(26, dtype="float32")
        for ch in t.lower():
            if "a" <= ch <= "z":
                v[ord(ch) - 97] += 1
        n = np.linalg.norm(v) or 1.0
        out.append(v / n)
    return np.vstack(out).astype("float32")


def test_index_persist_and_search_after_restart(monkeypatch, unique):
    monkeypatch.setattr(embeddings, "ENABLE_SEMANTIC_SEARCH", True)
    monkeypatch.setattr(embeddings, "embed_texts", _fake_embed)

    create_job(f"s1{unique}", "a.txt", f"h1{unique}", "/tmp/x", 1)
    create_job(f"s2{unique}", "b.txt", f"h2{unique}", "/tmp/x", 1)
    embeddings.index_job_text(f"s1{unique}", "zzzz qqqq xxxx " * 5)
    embeddings.index_job_text(f"s2{unique}", "apple banana cherry " * 5)

    # simulate a server restart: wipe the in-memory index, reload from the DB
    embeddings._matrix, embeddings._chunk_ids = None, []
    embeddings.load_index()

    hits = embeddings.semantic_search("zzzz qqqq", top_k=5)
    assert hits and hits[0] == f"s1{unique}"
