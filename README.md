# TA Guide AI

TA Guide AI answers questions in Indonesian using a thesis or final-project guide that you provide as a PDF. Students can use it to look up submission requirements, proposal steps, or formatting rules and check the cited pages in their own guide.

The local Streamlit app displays answers, PDF page references, and the passages used. A FastAPI backend also exposes the same workflow through an API.

## How It Works

1. **Index your guide.** The ingestion script extracts readable text from the PDF, groups it by section, and stores locally generated embeddings in ChromaDB.
2. **Retrieve relevant passages.** The backend combines semantic search with BM25 keyword search.
3. **Generate an answer.** Gemini receives the question and selected passages, including their source references. The PDF, embeddings, and search index stay on your machine. The backend returns a message when it finds no relevant material.

## Quick Start

You need Python 3.11 or 3.12, a [Gemini API key](https://aistudio.google.com/apikey), and one PDF with selectable text. Use an appropriate guide that you have permission to process with Gemini.

### 1. Set up the project

```bash
git clone https://github.com/AutoReXz/TA-Guide-AI.git
cd TA-Guide-AI
python -m venv .venv
```

Activate the virtual environment with `source .venv/bin/activate` on macOS or Linux, or `.\.venv\Scripts\Activate.ps1` in Windows PowerShell. Then install the dependencies:

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` using `cp .env.example .env` on macOS or Linux, or `Copy-Item .env.example .env` in PowerShell. Set `GEMINI_API_KEY` in `.env`.

### 2. Add and index your PDF

Place **exactly one PDF** directly in [`knowledge_base/`](knowledge_base/). Choose any filename ending in `.pdf`. The repository provides an empty folder for your guide, and Git ignores PDFs placed there.

```bash
python ingest.py
```

The first run downloads the local embedding model. To switch guides, stop the backend, replace the PDF, run the indexing command again, and restart the backend. Indexing replaces the previous document entries.

### 3. Start the API and chat

Run the backend from the repository root:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [the interactive API reference](http://localhost:8000/docs) to try `POST /ask` with `{"question":"Apa syarat seminar proposal?"}`. The [health endpoint](http://localhost:8000/health) reports whether the index and Gemini key are configured.

In a second terminal, activate the same virtual environment and start the chat:

```bash
python -m streamlit run app/streamlit_app.py
```

Open [the local chat](http://localhost:8501). Choose an example question or ask about the guide you indexed. Feedback you submit is saved locally in `feedback.jsonl`.

## Optional WhatsApp Integration

The repository includes an [n8n workflow](n8n/workflows/chatbot-panduan-ta.json) that forwards WhatsApp text messages to FastAPI. The local chat setup works independently. To use WhatsApp, configure your own Meta credentials and a reachable HTTPS webhook. See the [integration notes](PROJECT_CONTEXT.md#10-whatsapp-and-n8n-integration) and [workflow screenshot](dokumentasi/n8n-workflow.png).

## Tests

```bash
python -m unittest discover -s tests
```

The tests cover PDF discovery and extraction, hybrid retrieval, source references, and API responses. See the [project notes](PROJECT_CONTEXT.md) for architecture and implementation details.
