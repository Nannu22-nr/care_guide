"""Document processing: digital PDF, scanned/OCR, images, spreadsheets, corruption."""
import io
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

from extraction import extract_text

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None,
                                     reason="tesseract binary not installed")


def _make_pdf(pages):
    import fitz
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _text_image(text, size=(900, 300), fmt="PNG"):
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 40)
    except Exception:
        font = ImageFont.load_default()
    d.text((30, 100), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ------------------------------------------------------------------ PDF
def test_digital_pdf_multi_page():
    data = _make_pdf(["Invoice INV-001 total 100.00 " + "x" * 30,
                      "Page two content ACME Corp " + "y" * 30])
    out = extract_text(data, "multi.pdf")
    assert "INV-001" in out and "ACME Corp" in out
    assert "--- Page 1" in out and "--- Page 2" in out


@needs_tesseract
def test_scanned_pdf_uses_ocr():
    import fitz
    png = _text_image("INVOICE 7788 TOTAL 250")
    doc = fitz.open()
    page = doc.new_page(width=900, height=300)
    page.insert_image(page.rect, stream=png)   # image-only page, no text layer
    data = doc.tobytes()
    doc.close()
    out = extract_text(data, "scan.pdf")
    assert "(OCR)" in out
    assert "7788" in out


# --------------------------------------------------------------- images
@needs_tesseract
def test_png_ocr():
    out = extract_text(_text_image("INVOICE 4242"), "a.png")
    assert "4242" in out


@needs_tesseract
def test_multiframe_tiff():
    frames = [Image.open(io.BytesIO(_text_image(f"PAGE {n} CODE {n}{n}{n}"))) for n in (1, 2)]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    out = extract_text(buf.getvalue(), "multi.tiff")
    assert "Frame 1" in out and "Frame 2" in out


# --------------------------------------------------------- spreadsheets
def test_csv_extraction():
    out = extract_text(b"invoice_number,supplier,total\nINV-001,ACME,100.00\n", "t.csv")
    assert "INV-001" in out and "ACME" in out


def test_csv_latin1_fallback():
    out = extract_text("name,total\nCaf\xe9,5\n".encode("latin-1"), "l.csv")
    assert not out.startswith("ERROR:")
    assert "Caf" in out


def test_xlsx_multiple_sheets():
    import pandas as pd
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame({"inv": ["A-1"], "total": [10]}).to_excel(w, sheet_name="Jan", index=False)
        pd.DataFrame({"inv": ["B-2"], "total": [20]}).to_excel(w, sheet_name="Feb", index=False)
    out = extract_text(buf.getvalue(), "book.xlsx")
    assert "Sheet: Jan" in out and "Sheet: Feb" in out
    assert "A-1" in out and "B-2" in out


# ------------------------------------------------------- failure cases
@pytest.mark.parametrize("name,data", [
    ("bad.pdf", b"this is not a pdf"),
    ("bad.xlsx", b"this is not an excel file"),
    ("bad.png", b"this is not a png"),
    ("empty.csv", b""),
    ("test.exe", b"MZ..."),
])
def test_corrupted_or_unsupported_returns_error(name, data):
    out = extract_text(data, name)
    assert out.startswith("ERROR:"), f"{name} should fail cleanly, got: {out[:80]!r}"
