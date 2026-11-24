import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEEDBACK_FILE = PROJECT_ROOT / "feedback.jsonl"
SAMPLE_QUESTIONS = [
    "Apa saja persyaratan untuk mengajukan Tugas Akhir?",
    "Bagaimana tahapan seminar proposal?",
    "Apa format penulisan laporan Tugas Akhir?",
    "Bagaimana prosedur pendaftaran sidang?",
]


class BackendRequestError(Exception):
    pass


def ask_backend(api_url: str, question: str, timeout: int) -> tuple[dict[str, Any], float]:
    started_at = time.perf_counter()
    try:
        response = requests.post(
            f"{api_url.rstrip('/')}/ask",
            json={"question": question},
            timeout=timeout,
        )
    except requests.Timeout as error:
        raise BackendRequestError(
            f"Backend tidak merespons dalam {timeout} detik. "
            "Permintaan Gemini mungkin memerlukan waktu lebih lama; coba lagi."
        ) from error
    except requests.ConnectionError as error:
        raise BackendRequestError(
            "Tidak dapat terhubung ke FastAPI. Pastikan backend berjalan "
            "dan URL di sidebar sudah benar."
        ) from error
    except requests.RequestException as error:
        raise BackendRequestError(f"Permintaan ke backend gagal: {error}") from error

    elapsed = time.perf_counter() - started_at
    try:
        payload = response.json()
    except ValueError as error:
        if not response.ok:
            payload = {}
        else:
            raise BackendRequestError(
                "Backend mengembalikan respons yang bukan JSON."
            ) from error

    if not response.ok:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        detail_text = str(detail or response.text or "Tanpa rincian tambahan.")
        if response.status_code == 502:
            message = f"Gemini gagal memproses pertanyaan: {detail_text}"
        elif response.status_code >= 500:
            message = f"Backend mengalami kesalahan ({response.status_code}): {detail_text}"
        else:
            message = f"Permintaan ditolak ({response.status_code}): {detail_text}"
        raise BackendRequestError(message)

    if not isinstance(payload, dict) or not isinstance(payload.get("answer"), str):
        raise BackendRequestError(
            "Format respons backend tidak sesuai: field 'answer' tidak tersedia."
        )
    return payload, elapsed


def persist_feedback(message: dict[str, Any], rating: str) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": message["question"],
        "answer": message["content"],
        "rating": rating,
        "sources": message.get("sources", []),
        "elapsed_seconds": message.get("elapsed_seconds"),
    }
    with FEEDBACK_FILE.open("a", encoding="utf-8") as feedback_file:
        feedback_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def render_assistant_details(message: dict[str, Any]) -> None:
    elapsed = message.get("elapsed_seconds")
    if elapsed is not None:
        st.caption(f"Waktu respons: {elapsed:.2f} detik")

    sources = message.get("sources", [])
    if sources:
        source_labels = [
            f"{source.get('file', 'Sumber')} — hal. {source.get('page', '?')}"
            for source in sources
            if isinstance(source, dict)
        ]
        if source_labels:
            st.markdown("**Referensi PDF**")
            for label in source_labels:
                st.markdown(f"- {label}")
    else:
        st.caption("Backend tidak mengembalikan referensi halaman untuk jawaban ini.")

    chunks = message.get("chunks", [])
    with st.expander(f"Cuplikan sumber / chunks ({len(chunks)})"):
        if not chunks:
            st.caption("Backend tidak mengembalikan cuplikan untuk jawaban ini.")
        for index, chunk in enumerate(chunks, start=1):
            if not isinstance(chunk, dict):
                continue
            title = (
                f"{chunk.get('file', 'Sumber')} — hal. {chunk.get('page', '?')}"
            )
            score = chunk.get("relevance_score")
            if isinstance(score, (int, float)):
                title += f" · relevansi {score:.3f}"
            st.markdown(f"**{index}. {title}**")
            st.write(chunk.get("content", "Isi cuplikan tidak tersedia."))

    debug_info = message.get("debug")
    if debug_info is not None:
        with st.expander("Informasi debugging"):
            st.json(debug_info)

    feedback = message.get("feedback")
    if feedback:
        st.caption(f"Feedback tersimpan: {feedback}")
    else:
        st.caption("Apakah jawaban ini membantu evaluasi RAG?")
        col_correct, col_incorrect = st.columns(2)
        with col_correct:
            if st.button("✅ Benar", key=f"correct_{message['id']}"):
                try:
                    persist_feedback(message, "benar")
                except OSError as error:
                    st.error(f"Feedback tidak dapat disimpan ke lokal: {error}")
                else:
                    message["feedback"] = "benar"
                    st.rerun()
        with col_incorrect:
            if st.button("❌ Salah", key=f"incorrect_{message['id']}"):
                try:
                    persist_feedback(message, "salah")
                except OSError as error:
                    st.error(f"Feedback tidak dapat disimpan ke lokal: {error}")
                else:
                    message["feedback"] = "salah"
                    st.rerun()


st.set_page_config(
    page_title="Panduan TA | AI Chatbot",
    page_icon="🎓",
    layout="centered",
)

st.markdown(
    """
    <style>
    .block-container { max-width: 900px; padding-top: 2rem; }
    [data-testid="stChatMessage"] {
        border: 1px solid rgba(128, 128, 128, .15);
        border-radius: 16px;
        padding: 1rem 1.15rem;
        margin-bottom: .8rem;
    }
    [data-testid="stChatInput"] { padding-bottom: 1rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🎓 Asisten Panduan Tugas Akhir")
st.markdown(
    "Tanyakan hal seputar Panduan TA. Jawaban dan referensi diambil dari "
    "backend RAG, lalu beri feedback untuk membantu evaluasi."
)

with st.sidebar:
    st.header("Pengaturan")
    api_url = st.text_input(
        "URL FastAPI",
        value=os.getenv("API_BASE_URL", "http://localhost:8000"),
        help="Alamat backend yang menyediakan endpoint POST /ask.",
    )
    timeout_seconds = st.number_input(
        "Timeout (detik)",
        min_value=5,
        max_value=300,
        value=90,
        step=5,
    )
    st.divider()
    st.subheader("Contoh pertanyaan")
    for index, sample_question in enumerate(SAMPLE_QUESTIONS):
        if st.button(sample_question, key=f"sample_{index}", use_container_width=True):
            st.session_state["pending_question"] = sample_question
            st.rerun()
    st.caption("Feedback disimpan ke feedback.jsonl di folder proyek.")

if "messages" not in st.session_state:
    st.session_state["messages"] = []

for message in st.session_state["messages"]:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            if message.get("error"):
                elapsed = message.get("elapsed_seconds")
                if elapsed is not None:
                    st.caption(f"Waktu respons: {elapsed:.2f} detik")
            else:
                render_assistant_details(message)

prompt = st.chat_input("Tanyakan sesuatu tentang Panduan TA...")
question = prompt or st.session_state.pop("pending_question", None)
if question:
    st.session_state["messages"].append(
        {"id": uuid.uuid4().hex, "role": "user", "content": question}
    )
    request_started_at = time.perf_counter()
    with st.chat_message("assistant"):
        with st.spinner("Menyiapkan jawaban berdasarkan Panduan TA..."):
            try:
                answer_payload, elapsed_seconds = ask_backend(
                    api_url,
                    question,
                    int(timeout_seconds),
                )
                answer_message = {
                    "id": uuid.uuid4().hex,
                    "role": "assistant",
                    "content": answer_payload["answer"],
                    "question": question,
                    "sources": answer_payload.get("sources", []),
                    "chunks": answer_payload.get("chunks", []),
                    "debug": answer_payload.get("debug"),
                    "elapsed_seconds": elapsed_seconds,
                }
            except BackendRequestError as error:
                answer_message = {
                    "id": uuid.uuid4().hex,
                    "role": "assistant",
                    "content": str(error),
                    "error": True,
                    "question": question,
                    "elapsed_seconds": time.perf_counter() - request_started_at,
                }
    st.session_state["messages"].append(answer_message)
    st.rerun()
