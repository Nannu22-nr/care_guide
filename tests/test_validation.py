from datetime import date

import pytest

from validation import validate_extraction, normalize_confidence, to_decimal, parse_date


def _base_ext(**overrides):
    ext = {
        "invoice_number": "INV-1",
        "invoice_date": "2024-05-01",
        "supplier_name": "Acme",
        "customer_name": "Beta",
        "subtotal": 100.0,
        "tax": 10.0,
        "total": 110.0,
        "currency": "USD",
        "line_items": [{"description": "X", "quantity": 1, "unit_price": 100.0, "amount": 100.0}],
        "confidence": {"invoice_number": 0.9, "total": 0.9, "supplier_name": 0.9},
    }
    ext.update(overrides)
    return ext


def _v(**overrides):
    return validate_extraction(normalize_confidence(_base_ext(**overrides)), "", "hash",
                               duplicate_checker=None)


def _rules(v, key="errors"):
    return [e["rule"] for e in v[key]]


def test_valid_invoice():
    v = _v()
    assert v["valid"] is True and v["errors"] == []


def test_missing_invoice_number():
    assert "MISSING_FIELD" in _rules(_v(invoice_number=None))


def test_missing_total_needs_review():
    v = _v(total=None, subtotal=None, tax=None)
    assert v["needs_review"] is True


def test_missing_optional_fields_are_only_warnings():
    v = _v(customer_name=None, currency=None)
    assert v["valid"] is True
    assert "MISSING_FIELD" in _rules(v, "flags")


def test_total_mismatch():
    assert "TOTAL_MISMATCH" in _rules(_v(total=200.0))


def test_total_within_rounding_tolerance():
    assert _v(total=110.01)["valid"] is True


def test_negative_amount():
    assert "NEGATIVE_AMOUNT" in _rules(_v(subtotal=-10.0, total=-100.0))


def test_zero_total():
    assert "ZERO_TOTAL" in _rules(_v(subtotal=0.0, tax=0.0, total=0.0, line_items=None))


def test_non_numeric_amount():
    assert "NON_NUMERIC_AMOUNT" in _rules(_v(total="N/A"))


def test_line_items_sum_must_match_subtotal():
    items = [{"description": "X", "quantity": 1, "unit_price": 50.0, "amount": 50.0}]
    assert "LINE_ITEMS_SUBTOTAL_MISMATCH" in _rules(_v(line_items=items))


def test_line_items_sum_matches_with_multiple_items():
    items = [{"quantity": 1, "unit_price": 60.0, "amount": 60.0},
             {"quantity": 2, "unit_price": 20.0, "amount": 40.0}]
    assert _v(line_items=items)["valid"] is True


def test_line_item_internal_math_warning():
    items = [{"quantity": 2, "unit_price": 50.0, "amount": 100.0},
             {"quantity": 3, "unit_price": 10.0, "amount": 99.0}]
    v = _v(line_items=items, subtotal=199.0, total=209.0)
    assert "LINE_ITEM_INTERNAL_MISMATCH" in _rules(v, "flags")


def test_tax_rate_implausible():
    assert "TAX_RATE_IMPLAUSIBLE" in _rules(_v(tax=80.0, total=180.0), "flags")


def test_date_unparseable():
    assert "DATE_UNPARSEABLE" in _rules(_v(invoice_date="not-a-date"), "flags")


def test_date_in_future():
    v = validate_extraction(normalize_confidence(_base_ext(invoice_date="2030-01-01")), "", "h",
                            today=date(2026, 1, 1))
    assert "DATE_IN_FUTURE" in _rules(v, "flags")


def test_date_implausibly_old():
    assert "DATE_IMPLAUSIBLE" in _rules(_v(invoice_date="1985-03-04"), "flags")


def test_duplicate_invoice_detected():
    def checker(fh, sup, inv):
        return {"file_dup": False, "invoice_dup": True}
    v = validate_extraction(normalize_confidence(_base_ext()), "", "hash", duplicate_checker=checker)
    assert "DUPLICATE_INVOICE" in _rules(v)


def test_duplicate_file_detected():
    def checker(fh, sup, inv):
        return {"file_dup": True, "invoice_dup": False}
    v = validate_extraction(normalize_confidence(_base_ext()), "", "hash", duplicate_checker=checker)
    assert "DUPLICATE_FILE" in _rules(v)


def test_very_low_confidence_is_error():
    assert "VERY_LOW_CONFIDENCE" in _rules(_v(confidence={"total": 0.2, "invoice_number": 0.9, "supplier_name": 0.9}))


def test_string_amounts_are_parsed():
    v = _v(subtotal="1,000.00", tax="100", total="$1,100.00",
           line_items=[{"amount": "1000"}])
    assert v["valid"] is True


@pytest.mark.parametrize("raw,expected", [("1,234.50", "1234.50"), ("$ 99", "99"), (None, None), ("abc", None), (True, None)])
def test_to_decimal(raw, expected):
    got = to_decimal(raw)
    assert (str(got) if got is not None else None) == expected


def test_parse_date_formats():
    assert parse_date("2024-05-01") == date(2024, 5, 1)
    assert parse_date("01/05/2024") == date(2024, 5, 1)
    assert parse_date("garbage") is None
