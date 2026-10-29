import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# LLM
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

# App
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "50"))
UPLOAD_DIR = BASE_DIR / os.getenv("UPLOAD_DIR", "data/uploads")
DB_PATH = BASE_DIR / os.getenv("DB_PATH", "data/app.db")
INDEX_DIR = BASE_DIR / os.getenv("INDEX_DIR", "data/index")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
INDEX_DIR.mkdir(parents=True, exist_ok=True)

# Security
# If API_KEY is empty, auth is disabled (development mode only).
API_KEY = os.getenv("API_KEY", "")
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
# Comma separated list of allowed CORS origins. Empty = same-origin only.
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()
]

# Worker pool size for background processing
WORKER_THREADS = int(os.getenv("WORKER_THREADS", "2"))

# Features
ENABLE_SEMANTIC_SEARCH = os.getenv("ENABLE_SEMANTIC_SEARCH", "true").lower() == "true"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# Allowed
ALLOWED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif",
    ".xlsx", ".xls", ".csv",
}
ALLOWED_MIME_PREFIXES = {
    "application/pdf", "image/jpeg", "image/png", "image/tiff",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv", "text/plain", "application/csv",
}

# Validation tolerances (absolute, in currency units)
AMOUNT_TOLERANCE = float(os.getenv("AMOUNT_TOLERANCE", "0.02"))
LOW_CONFIDENCE_THRESHOLD = float(os.getenv("LOW_CONFIDENCE_THRESHOLD", "0.7"))
# Invoices dated before this year are flagged as implausible
MIN_PLAUSIBLE_YEAR = int(os.getenv("MIN_PLAUSIBLE_YEAR", "2000"))
