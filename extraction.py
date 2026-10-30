import io
import pdfplumber
import fitz  # PyMuPDF
import pytesseract
from PIL import Image, ImageSequence
import pandas as pd

# OpenCV preprocessing (optional)
try:
    from preprocess import preprocess_image_for_ocr
    _HAS_CV2 = True
except Exception:
    _HAS_CV2 = False

    def preprocess_image_for_ocr(b):
        return b

MAX_PAGES = 5000
OCR_DPI = 250

GOOD_OCR_CONFIDENCE = 80.0


# --------------------------------------------------------------------- OCR
def _ocr_with_confidence(img: Image.Image) -> tuple[str, float]:
    """Run tesseract once; return (text, mean word confidence 0-100)."""
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    words, confs = [], []
    for txt, conf in zip(data["text"], data["conf"]):
        try:
            c = float(conf)
        except (TypeError, ValueError):
            continue
        if c >= 0 and str(txt).strip():
            words.append(txt)
            confs.append(c)
    mean_conf = sum(confs) / len(confs) if confs else 0.0
    return pytesseract.image_to_string(img), mean_conf


def ocr_image_bytes(img_bytes: bytes) -> str:
    """OCR one image. Plain OCR first; preprocess only if confidence is low,
    then keep whichever variant scored the higher mean confidence."""
    raw_img = Image.open(io.BytesIO(img_bytes))
    raw_text, raw_conf = _ocr_with_confidence(raw_img)
    if raw_conf >= GOOD_OCR_CONFIDENCE or not _HAS_CV2:
        return raw_text
    try:
        pre_img = Image.open(io.BytesIO(preprocess_image_for_ocr(img_bytes)))
        pre_text, pre_conf = _ocr_with_confidence(pre_img)
    except Exception:
        return raw_text
    return pre_text if pre_conf > raw_conf else raw_text


# --------------------------------------------------------------------- PDF
def extract_pdf_text(file_bytes: bytes) -> str:
    """Extract text from a PDF. Pages with (almost) no text layer are OCR'd
    individually, so mixed digital + scanned PDFs work."""
    parts = []
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            n = len(doc)
            if n > MAX_PAGES:
                return f"ERROR: PDF too large ({n} pages > {MAX_PAGES})"
            for i, page in enumerate(doc):
                text = page.get_text("text", sort=True) or ""
                parts.append((i, text))
        finally:
            doc.close()
    except Exception as e:
        # PyMuPDF failed -> try pdfplumber
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for i, page in enumerate(pdf.pages):
                    parts.append((i, page.extract_text() or ""))
        except Exception as e2:
            return f"ERROR: PDF parse failed: {e} | {e2}"

    if not parts:
        return "ERROR: PDF has no pages"

    # Whole document has no usable text layer -> OCR everything
    if len("".join(t for _, t in parts).strip()) < 100 and all(len(t.strip()) < 20 for _, t in parts):
        try:
            return ocr_pdf_pages(file_bytes)
        except Exception as e:
            return f"ERROR: OCR fallback failed: {e}"

    # Mixed documents: OCR only the pages that lack a text layer
    final = []
    for i, t in parts:
        if len(t.strip()) < 20:
            final.append(f"--- Page {i+1} (OCR) ---\n{_ocr_pdf_page(file_bytes, i)}")
        else:
            final.append(f"--- Page {i+1} ---\n{t}")
    return "\n".join(final)


def _render_page_png(doc, index: int) -> bytes:
    return doc[index].get_pixmap(dpi=OCR_DPI).tobytes("png")


def _ocr_pdf_page(file_bytes: bytes, page_index: int) -> str:
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            return ocr_image_bytes(_render_page_png(doc, page_index))
        finally:
            doc.close()
    except Exception as e:
        return f"[OCR failed: {e}]"


def ocr_pdf_pages(file_bytes: bytes) -> str:
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    out = []
    try:
        for i in range(len(doc)):
            try:
                text = ocr_image_bytes(_render_page_png(doc, i))
                out.append(f"--- Page {i+1} (OCR) ---\n{text}")
            except Exception as e:
                out.append(f"--- Page {i+1} (OCR failed: {e}) ---")
    finally:
        doc.close()
    return "\n".join(out)


# ------------------------------------------------------------------- images
def extract_image_text(file_bytes: bytes) -> str:
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if getattr(img, "n_frames", 1) > 1:          # multi-page TIFF
            out = []
            for i, frame in enumerate(ImageSequence.Iterator(img)):
                buf = io.BytesIO()
                frame.convert("RGB").save(buf, format="PNG")
                out.append(f"--- Frame {i+1} ---\n{ocr_image_bytes(buf.getvalue())}")
            return "\n".join(out)

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")    # normalise odd modes (CMYK, P, 16-bit)
        return ocr_image_bytes(buf.getvalue())
    except Exception as e:
        return f"ERROR: Image OCR failed: {e}"


# -------------------------------------------------------------- spreadsheets
def extract_spreadsheet_text(file_bytes: bytes, filename: str) -> str:
    out = []
    try:
        if filename.lower().endswith(".csv"):
            try:
                df = pd.read_csv(io.BytesIO(file_bytes))
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(file_bytes), encoding="latin-1")
            out.append(df.to_string(index=False))
        else:
            # header=None keeps "label | value" style sheets intact (no row is
            # swallowed as a header); every sheet is read.
            xls = pd.ExcelFile(io.BytesIO(file_bytes))
            for sheet in xls.sheet_names:
                try:
                    df = xls.parse(sheet, header=None)
                    df = df.dropna(how="all").dropna(axis=1, how="all")
                    out.append(f"--- Sheet: {sheet} ---\n{df.to_string(index=False, header=False)}")
                except Exception as e:
                    out.append(f"--- Sheet: {sheet} (failed: {e}) ---")
    except Exception as e:
        return f"ERROR: Spreadsheet parse failed: {e}"
    return "\n".join(out)


# ---------------------------------------------------------------- dispatcher
def extract_text(file_bytes: bytes, filename: str) -> str:
    low = filename.lower()
    if low.endswith(".pdf"):
        return extract_pdf_text(file_bytes)
    elif low.endswith((".jpg", ".jpeg", ".png", ".tiff", ".tif")):
        return extract_image_text(file_bytes)
    elif low.endswith((".xlsx", ".xls", ".csv")):
        return extract_spreadsheet_text(file_bytes, filename)
    return f"ERROR: Unsupported file type: {filename}"
