import json
import time
import hashlib
from groq import Groq
from pydantic import BaseModel, ValidationError, field_validator
from typing import Optional
from config import GROQ_API_KEY, GROQ_MODEL

INVOICE_FIELDS = [
    "invoice_number", "invoice_date", "supplier_name", "customer_name",
    "subtotal", "tax", "total", "currency", "line_items",
]

PROMPT_VERSION = "v1.2"
SYSTEM_PROMPT = """You are a precise invoice extraction engine. Extract ONLY the specified fields from the text.
Return a single JSON object. Rules:
1. NEVER invent values. If a field is not clearly present, use null.
2. Include a "confidence" object with scores (0.0-1.0) for each extracted field.
3. Currency: Use ISO code (USD, EUR, GBP, BDT) if identifiable.
4. invoice_date: Use ISO format (YYYY-MM-DD) if possible.
5. line_items: Array of {description, quantity, unit_price, amount}. Use null for missing sub-fields.
6. If text is not an invoice, return nulls and set notes to "Not an invoice".

Fields: invoice_number, invoice_date, supplier_name, customer_name, subtotal, tax, total, currency, line_items.
Return JSON only."""


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class InvoiceSchema(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    supplier_name: Optional[str] = None
    customer_name: Optional[str] = None
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    currency: Optional[str] = None
    line_items: Optional[list[LineItem]] = None
    confidence: Optional[dict] = None
    notes: Optional[str] = None

    @field_validator("subtotal", "tax", "total", mode="before")
    @classmethod
    def coerce_num(cls, v):
        if v is None or v == "":
            return None
        try:
            return float(str(v).replace(",", "").replace("$", "").replace("€", "").strip())
        except Exception:
            return None


_client = None
_cache: dict[str, dict] = {}


def _get_client():
    global _client
    if _client is None:
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set")
        _client = Groq(api_key=GROQ_API_KEY.strip(), timeout=60.0, max_retries=2)
    return _client


def _chunk_text(text: str, chunk_size: int = 40000) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i:i + chunk_size])
        i += chunk_size
    return chunks


def _cache_key(text: str) -> str:
    return hashlib.sha256((PROMPT_VERSION + text).encode()).hexdigest()


def extract_with_groq(raw_text: str, max_retries: int = 2) -> dict:
    if not raw_text or not raw_text.strip():
        return {"_error": "Empty input"}

    key = _cache_key(raw_text)
    if key in _cache:
        return _cache[key]

    # Long text: chunk and merge (better than blind truncation)
    if len(raw_text) > 60000:
        chunks = _chunk_text(raw_text)
        results = [_call_groq(c, max_retries) for c in chunks]
        merged = _merge_results(results)
    else:
        merged = _call_groq(raw_text, max_retries)

    # Validate against schema
    if "_error" not in merged:
        try:
            InvoiceSchema(**merged)
        except ValidationError as e:
            merged["_schema_warnings"] = str(e)[:300]

    _cache[key] = merged
    return merged


def _call_groq(text: str, max_retries: int) -> dict:
    client = _get_client()
    user_msg = f"Extract invoice data from this text:\n\n{text}"
    content = ""
    for attempt in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                max_tokens=4096,
            )
            content = resp.choices[0].message.content
            return json.loads(content)
        except json.JSONDecodeError:
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"_error": "LLM returned invalid JSON", "_raw": content[:500]}
        except Exception as e:
            cause = repr(getattr(e, "__cause__", None))
            print(f"[LLM ERROR] {type(e).__name__}: {e} | cause={cause} | "
                  f"model={GROQ_MODEL!r} | key_len={len(GROQ_API_KEY)}", flush=True)
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"_error": f"{e} ({type(e).__name__}: {cause})"}


def _merge_results(results: list[dict]) -> dict:
    """Merge multiple chunk extractions. Prefer non-null; merge line items."""
    merged = {f: None for f in INVOICE_FIELDS}
    merged["confidence"] = {}
    items: list = []

    for r in results:
        if "_error" in r:
            continue
        for f in INVOICE_FIELDS:
            v = r.get(f)
            if f == "line_items":
                if isinstance(v, list):
                    items.extend(v)
                continue
            if merged[f] is None and v is not None:
                merged[f] = v
        for f, c in (r.get("confidence") or {}).items():
            if isinstance(c, (int, float)) and c > merged["confidence"].get(f, 0):
                merged["confidence"][f] = c

    merged["line_items"] = items or None
    return merged