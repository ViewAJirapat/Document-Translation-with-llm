FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    DOCLING_MODELS=/opt/docling-models

# tesseract: OCR for scanned pages.
# pango/harfbuzz: WeasyPrint PDF rendering (Pango shapes Thai and breaks Thai lines via libthai).
# fonts: Thai fonts for the exported PDF (TLWG Laksaman ~ TH Sarabun New, Noto Sans/Serif Thai).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
        fonts-thai-tlwg fonts-noto-core \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The extra index supplies CPU-only PyTorch ("+cpu" builds win over PyPI's CUDA builds), saving several GB.
COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# Fetch docling's layout and table models now so the first job doesn't stall on a download.
RUN docling-tools models download -o "$DOCLING_MODELS" layout tableformer

COPY app ./app

RUN useradd --create-home appuser && mkdir -p /data && chown appuser /data
USER appuser
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
