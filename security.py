import magic
from config import ALLOWED_MIME_PREFIXES, ALLOWED_EXTENSIONS
from pathlib import Path


def validate_file_content(data: bytes, filename: str) -> tuple[bool, str]:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"Extension not allowed: {ext}"

    # Basic size checks
    if len(data) == 0:
        return False, "Empty file"
    if len(data) > 1024 * 1024 * 1024:  # hard cap 1GB
        return False, "File exceeds absolute 1GB cap"

    # MIME sniffing
    try:
        mime = magic.from_buffer(data[:8192], mime=True)
    except Exception:
        mime = "application/octet-stream"

    if not any(mime.startswith(p) or mime == p for p in ALLOWED_MIME_PREFIXES):
        # Some CSVs / xls may be reported as octet-stream; allow if extension matches
        if ext in {".csv", ".xls", ".xlsx"} and "octet-stream" in mime:
            return True, mime
        return False, f"MIME type not allowed: {mime}"

    return True, mime