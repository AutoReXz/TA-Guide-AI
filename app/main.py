from fastapi import FastAPI, HTTPException
from google.api_core.exceptions import GoogleAPIError
from pydantic import BaseModel, Field

from app.rag import answer_question, index_is_ready
from app.config import GEMINI_API_KEY

app = FastAPI(
    title="Chatbot Panduan Tugas Akhir",
    description="RAG lokal untuk menjawab pertanyaan berdasarkan PDF Panduan TA.",
    version="1.0.0",
)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


@app.get("/health")
def health() -> dict[str, object]:
    ready = index_is_ready()
    gemini_configured = bool(GEMINI_API_KEY)
    return {
        "status": "ok" if ready and gemini_configured else "degraded",
        "index_ready": ready,
        "gemini_configured": gemini_configured,
    }


@app.post("/ask")
def ask(request: AskRequest) -> dict:
    if not index_is_ready():
        raise HTTPException(
            status_code=503,
            detail="Indeks Panduan TA belum tersedia. Jalankan `python ingest.py`.",
        )
    if not request.question.strip():
        raise HTTPException(
            status_code=422,
            detail="Pertanyaan tidak boleh hanya berisi spasi.",
        )
    try:
        return answer_question(request.question.strip())
    except GoogleAPIError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Gemini gagal memproses permintaan: {error}",
        ) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
