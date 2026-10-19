"""Settings, read once from environment variables (see .env.example)."""
import os
from pathlib import Path

LLAMA_URL = os.environ.get("LLAMA_URL", "http://host.docker.internal:8080").rstrip("/")
LLAMA_API_KEY = os.environ.get("LLAMA_API_KEY", "")
LLAMA_TIMEOUT = float(os.environ.get("LLAMA_TIMEOUT", "600"))

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "app.db"
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "200"))

DOCLING_MODELS = os.environ.get("DOCLING_MODELS", "/opt/docling-models")

DOCX_FONT = os.environ.get("DOCX_FONT", "Leelawadee UI")
DOCX_FONT_SIZE = float(os.environ.get("DOCX_FONT_SIZE", "11"))
PDF_FONT = os.environ.get("PDF_FONT", '"Sarabun", "Laksaman", "Noto Serif Thai", serif')
