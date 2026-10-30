
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from config import AMOUNT_TOLERANCE, LOW_CONFIDENCE_THRESHOLD, MIN_PLAUSIBLE_YEAR
from llm import INVOICE_FIELDS

VERY_LOW_CONFIDENCE = 0.5
MAX_PLAUSIBLE_TAX_RATE = Decimal("0.5")  # tax > 50% of subtotal is suspicious

_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y",
    "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
)


# ---------------------------------------------------------------- helpers
def normalize_confidence(ext: dict) -> dict:
    """Ensure `ext["confidence"]` holds a float for every INVOICE_FIELD.

    Absent values get 0.0; present values without a usable score get 0.5.
    """
    conf = ext.get("confidence")
    if not isinstance(conf, dict):
        conf = {}
    for f in INVOICE_FIELDS:
        val = ext.get(f)
        is_absent = (
            val is None
            or (isinstance(val, str) and val.strip() == "")
            or (isinstance(val, list) and len(val) == 0)
        )
        if is_absent:
            conf[f] = 0.0
        elif f not in conf or not isinstance(conf[f], (int, float)):
            conf[f] = 0.5
    ext["confidence"] = {k: v for k, v in conf.items() if k in INVOICE_FIELDS}
    return ext


def to_decimal(v) -> Decimal | None:
    """Parse numbers like 1234.5, '1,234.50', '$ 99' into Decimal, else None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        try:
            return Decimal(str(v))
        except InvalidOperation:
            return None
    s = re.sub(r"[^\d.\-]", "", str(v).replace(",", ""))
    if s in ("", "-", ".", "-."):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_date(value) -> date | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _is_missing(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _issue(rule, field, message, severity):
    return {"rule": rule, "field": field, "message": message, "severity": severity}


# ------------------------------------------------------------------ main
def validate_extraction(ext, raw_text, file_hash, duplicate_checker=None, today=None):
    """Validate one extraction. Returns
    {"errors": [...], "flags": [...], "needs_review": bool, "valid": bool}.
    """
    errors, flags = [], []
    tol = Decimal(str(AMOUNT_TOLERANCE))
    today = today or date.today()

    # --- Rule 1: required fields (blocking) ---------------------------------
    for f in ("invoice_number", "total"):
        val = ext.get(f)
        if _is_missing(val):
            errors.append(_issue("MISSING_FIELD", f,
                                 f"Required field '{f}' is missing", "ERROR"))
        elif f == "total" and to_decimal(val) == 0:
            errors.append(_issue("ZERO_TOTAL", f, "Total is 0", "ERROR"))

    # --- Rule 1b: recommended fields (non-blocking) -------------------------
    for f in ("invoice_date", "supplier_name", "customer_name", "currency"):
        if _is_missing(ext.get(f)):
            flags.append(_issue("MISSING_FIELD", f,
                                f"Field '{f}' is missing", "WARN"))

    sub = to_decimal(ext.get("subtotal"))
    tax = to_decimal(ext.get("tax"))
    tot = to_decimal(ext.get("total"))

    # Non-numeric garbage in a numeric field (value present but unparseable)
    for f, parsed in (("subtotal", sub), ("tax", tax), ("total", tot)):
        if not _is_missing(ext.get(f)) and parsed is None:
            errors.append(_issue("NON_NUMERIC_AMOUNT", f,
                                 f"'{f}' is not a number: {ext.get(f)!r}", "ERROR"))

    # --- Rule 2: subtotal + tax == total ------------------------------------
    if sub is not None and tot is not None:
        eff_tax = tax if tax is not None else Decimal("0")
        expected = sub + eff_tax
        if abs(expected - tot) > tol:
            errors.append(_issue(
                "TOTAL_MISMATCH", "total",
                f"subtotal({sub}) + tax({eff_tax}) = {expected}, but total = {tot}",
                "ERROR"))
        elif tax is None:
            flags.append(_issue("TAX_ASSUMED_ZERO", "tax",
                                "Tax is null; treated as 0.", "INFO"))

    # --- Rule 3: negative amounts -------------------------------------------
    for f, v in (("subtotal", sub), ("tax", tax), ("total", tot)):
        if v is not None and v < 0:
            errors.append(_issue("NEGATIVE_AMOUNT", f,
                                 f"{f} is negative ({v})", "ERROR"))

    # --- Rule 4: tax plausibility -------------------------------------------
    if sub is not None and tax is not None and sub > 0 and tax >= 0:
        if tax / sub > MAX_PLAUSIBLE_TAX_RATE:
            flags.append(_issue(
                "TAX_RATE_IMPLAUSIBLE", "tax",
                f"Tax is {tax / sub:.0%} of subtotal; please verify", "WARN"))

    # --- Rule 5: line items -------------------------------------------------
    items = ext.get("line_items") or []
    amounts = []
    if isinstance(items, list):
        for idx, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            amt = to_decimal(it.get("amount"))
            qty = to_decimal(it.get("quantity"))
            up = to_decimal(it.get("unit_price"))
            if amt is not None:
                amounts.append(amt)
            if qty is not None and up is not None and amt is not None:
                expected = qty * up
                if abs(expected - amt) > tol + Decimal("0.01"):
                    flags.append(_issue(
                        "LINE_ITEM_INTERNAL_MISMATCH", "line_items",
                        f"Item {idx + 1}: qty({qty}) x price({up}) = {expected} != {amt}",
                        "WARN"))

    # Rule 5b: sum(line item amounts) must agree with subtotal
    # (rounding slack grows slightly with the number of items)
    if amounts and isinstance(items, list) and len(amounts) == len(items):
        items_sum = sum(amounts, Decimal("0"))
        reference, ref_name = None, None
        if sub is not None:
            reference, ref_name = sub, "subtotal"
        elif tot is not None and tax is not None:
            reference, ref_name = tot - tax, "total - tax"
        if reference is not None:
            slack = tol + Decimal("0.01") * len(amounts)
            if abs(items_sum - reference) > slack:
                errors.append(_issue(
                    "LINE_ITEMS_SUBTOTAL_MISMATCH", "line_items",
                    f"Sum of line items = {items_sum}, but {ref_name} = {reference}",
                    "ERROR"))
    elif ext.get("subtotal") is not None and not items:
        flags.append(_issue("NO_LINE_ITEMS", "line_items",
                            "No line items extracted; subtotal could not be cross-checked",
                            "INFO"))

    # --- Rule 6: date sanity ------------------------------------------------
    raw_date = ext.get("invoice_date")
    if not _is_missing(raw_date):
        d = parse_date(raw_date)
        if d is None:
            flags.append(_issue("DATE_UNPARSEABLE", "invoice_date",
                                f"Could not parse date: {raw_date!r}", "WARN"))
        elif d > today:
            flags.append(_issue("DATE_IN_FUTURE", "invoice_date",
                                f"Invoice date {d.isoformat()} is in the future", "WARN"))
        elif d.year < MIN_PLAUSIBLE_YEAR:
            flags.append(_issue("DATE_IMPLAUSIBLE", "invoice_date",
                                f"Invoice date {d.isoformat()} is before {MIN_PLAUSIBLE_YEAR}",
                                "WARN"))

    # --- Rule 7: duplicates -------------------------------------------------
    if duplicate_checker is not None:
        sup = (ext.get("supplier_name") or "").strip().lower()
        inv = str(ext.get("invoice_number") or "").strip().lower()
        dup = duplicate_checker(file_hash, sup, inv) or {}
        if dup.get("file_dup"):
            errors.append(_issue("DUPLICATE_FILE", None,
                                 "Exact file already processed", "ERROR"))
        if dup.get("invoice_dup"):
            errors.append(_issue("DUPLICATE_INVOICE", "invoice_number",
                                 f"Invoice '{inv}' from '{sup}' already exists", "ERROR"))

    # --- Rule 8: low confidence --------------------------------------------
    conf = ext.get("confidence") or {}
    for f in ("invoice_number", "total", "supplier_name"):
        score = conf.get(f, 1.0)
        if not isinstance(score, (int, float)) or _is_missing(ext.get(f)):
            continue  # already reported as missing
        if score < VERY_LOW_CONFIDENCE:
            errors.append(_issue("VERY_LOW_CONFIDENCE", f,
                                 f"Model confidence extremely low ({score:.2f})", "ERROR"))
        elif score < LOW_CONFIDENCE_THRESHOLD:
            flags.append(_issue("LOW_CONFIDENCE", f,
                                f"Model confidence is low ({score:.2f})", "WARN"))

    needs_review = any(e["severity"] == "ERROR" for e in errors)
    return {
        "errors": errors,
        "flags": flags,
        "needs_review": needs_review,
        "valid": not needs_review,
    }
