# Official Python slim image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# tesseract-ocr : OCR engine
# libmagic1     : python-magic (MIME sniffing)
# libgl1, libglib2.0-0 : OpenCV runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libmagic1 \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user; only /app/data is writable
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p data/uploads data/index \
    && chown -R appuser:appuser /app/data
USER appuser

EXPOSE $PORT

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD curl -fs http://localhost:${PORT:-8000}/api/health || exit 1

# $PORT lets cloud platforms (Render/Railway) assign the port
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
