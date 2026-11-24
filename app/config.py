import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

CHROMA_DIR = Path(os.getenv("CHROMA_DIR", "chroma_db"))
if not CHROMA_DIR.is_absolute():
    CHROMA_DIR = ROOT_DIR / CHROMA_DIR
EMBEDDING_CACHE_DIR = ROOT_DIR / ".cache" / "fastembed"

COLLECTION_NAME = "panduan_ta"
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
TOP_K = int(os.getenv("TOP_K", "5"))
MIN_RELEVANCE_SCORE = float(os.getenv("MIN_RELEVANCE_SCORE", "0.45"))

if TOP_K < 1:
    raise ValueError("TOP_K harus lebih besar dari 0.")
if not 0 <= MIN_RELEVANCE_SCORE <= 1:
    raise ValueError("MIN_RELEVANCE_SCORE harus berada di antara 0 dan 1.")
