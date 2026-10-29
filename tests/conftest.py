import os
import sys
import tempfile
import uuid
from pathlib import Path

# Isolated temp storage + no model download / no real LLM during tests
_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["UPLOAD_DIR"] = os.path.join(_tmp, "uploads")
os.environ["INDEX_DIR"] = os.path.join(_tmp, "index")
os.environ["ENABLE_SEMANTIC_SEARCH"] = "false"
os.environ["GROQ_API_KEY"] = ""      # tests must never call the real API
os.environ["API_KEY"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from db import init_db


@pytest.fixture(autouse=True)
def _setup_db():
    init_db()
    yield


@pytest.fixture
def unique():
    """A short unique token so tests never collide on file hash / invoice no."""
    return uuid.uuid4().hex[:8]


GOOD_LLM_RESULT = {
    "invoice_number": "INV-1001",
    "invoice_date": "2024-05-01",
    "supplier_name": "Acme Corp",
    "customer_name": "Beta Ltd",
    "subtotal": 900.0,
    "tax": 90.0,
    "total": 990.0,
    "currency": "USD",
    "line_items": [
        {"description": "Widget A", "quantity": 3, "unit_price": 100.0, "amount": 300.0},
        {"description": "Widget B", "quantity": 2, "unit_price": 300.0, "amount": 600.0},
    ],
    "confidence": {"invoice_number": 0.95, "total": 0.95, "supplier_name": 0.9},
}
