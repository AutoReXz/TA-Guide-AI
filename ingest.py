import hashlib
from pathlib import Path
import re
from typing import cast

import chromadb
import pymupdf
import pymupdf4llm
from langchain_chroma import Chroma
from langchain_core.documents import Document

from app.config import CHROMA_DIR, COLLECTION_NAME, ROOT_DIR
from app.rag import get_embeddings

CHUNK_SIZE = 1600
CHUNK_OVERLAP = 180
SECTION_HEADING = re.compile(r"^\d+(?:\.\d+){1,3}\.?\s+\S")
CHAPTER_HEADING = re.compile(r"^BAB\s+(?:[IVXLCDM]+|\d+)\b", re.IGNORECASE)
PAGE_MARKER = re.compile(r"^(?:\d{1,4}|[ivxlcdm]{1,8})$", re.IGNORECASE)
MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
CONTENTS_TITLE = re.compile(
    r"^\s*(?:#{1,6}\s*)?DAFTAR\s+(?:ISI|TABEL|GAMBAR)\s*$",
    re.IGNORECASE,
)
CONTENTS_ENTRY = re.compile(
    r"^\s*(?:(?:BAB\s+[\w.-]+|[\d.]+|(?:Tabel|Gambar)\s+\S+)\s+)?"
    r".{1,100}(?:\.{2,}|…{2,})\s*\d{1,4}\s*$",
    re.IGNORECASE,
)


def find_pdf() -> Path:
    pdfs = sorted(ROOT_DIR.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(
            f"Tidak ada file PDF di folder proyek: {ROOT_DIR}"
        )
    if len(pdfs) > 1:
        names = ", ".join(pdf.name for pdf in pdfs)
        raise RuntimeError(
            f"Ditemukan lebih dari satu PDF di folder proyek ({names}). "
            "Sisakan satu panduan PDF agar sumber indeks tidak ambigu."
        )
    return pdfs[0]


def _is_heading(line: str) -> bool:
    return bool(
        MARKDOWN_HEADING.match(line)
        or SECTION_HEADING.match(line)
        or CHAPTER_HEADING.match(line)
    )


def _heading(line: str) -> tuple[int, str] | None:
    markdown_heading = MARKDOWN_HEADING.match(line)
    if markdown_heading:
        title = markdown_heading.group(2).strip().strip("*_` ")
        return len(markdown_heading.group(1)), title
    if CHAPTER_HEADING.match(line):
        return 1, line
    section_heading = SECTION_HEADING.match(line)
    if section_heading:
        numbering = re.match(r"\d+(?:\.\d+)*", line)
        assert numbering is not None
        return numbering.group(0).count(".") + 1, line
    return None


def _is_contents_page(text: str, following_contents: bool) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    plain_lines = [
        re.sub(r"^#{1,6}\s*", "", line).strip().strip("*_` ")
        for line in lines
    ]
    if any(CONTENTS_TITLE.fullmatch(line) for line in plain_lines):
        return True
    entry_count = sum(bool(CONTENTS_ENTRY.match(line)) for line in lines)
    if following_contents:
        return entry_count >= 2 and entry_count / max(len(lines), 1) >= 0.3
    return entry_count >= 4 and entry_count / max(len(lines), 1) >= 0.6


def _page_blocks(
    pages: list[tuple[int, str]],
) -> list[tuple[str, tuple[int, ...], bool]]:
    blocks: list[tuple[str, tuple[int, ...], bool]] = []
    lines: list[tuple[str, int]] = []

    def flush_block() -> None:
        if not lines:
            return
        content = "\n".join(line for line, _ in lines).strip()
        if content and not all(PAGE_MARKER.fullmatch(line.strip()) for line, _ in lines):
            blocks.append(
                (content, tuple(sorted({page for _, page in lines})), False)
            )
        lines.clear()

    for page_number, text in pages:
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                flush_block()
                continue
            if PAGE_MARKER.fullmatch(line.strip()):
                continue
            if _is_heading(line):
                flush_block()
                blocks.append((line.strip(), (page_number,), True))
            else:
                lines.append((line, page_number))
        flush_block()
    flush_block()
    return blocks


def _overlap_tail(text: str) -> str:
    if len(text) <= CHUNK_OVERLAP:
        return text
    start = len(text) - CHUNK_OVERLAP
    while start < len(text) and not text[start - 1].isspace():
        start += 1
    return text[start:] if start < len(text) else text[-CHUNK_OVERLAP:]


def _chunks_from_blocks(
    blocks: list[tuple[str, tuple[int, ...], bool]], source: str
) -> list[Document]:
    chunks: list[Document] = []
    heading_stack: list[tuple[int, str]] = []
    body: list[tuple[str, tuple[int, ...]]] = []

    def flush_chunk() -> str:
        if not body:
            return ""
        body_text = "\n\n".join(text for text, _ in body).strip()
        heading_lines = [line for _, line in heading_stack]
        content = "\n\n".join([*heading_lines, body_text]).strip()
        pages = sorted({page for _, block_pages in body for page in block_pages})
        if content:
            metadata: dict[str, str | int | list[int]] = {
                "source": source,
                "page": pages[0],
                "pages": pages,
            }
            if heading_stack:
                metadata["section"] = heading_stack[-1][1]
                metadata["section_hierarchy"] = " > ".join(
                    heading for _, heading in heading_stack
                )
            chunks.append(Document(page_content=content, metadata=metadata))
        return body_text

    for text, pages, is_heading in blocks:
        if is_heading:
            flush_chunk()
            body.clear()
            heading_info = _heading(text)
            if heading_info is None:
                raise ValueError(f"Format heading Markdown tidak valid: {text}")
            level, title = heading_info
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            continue

        fragments = []
        pending_fragments = [text]
        while pending_fragments:
            fragment = pending_fragments.pop(0)
            if len(fragment) <= CHUNK_SIZE:
                fragments.append(fragment)
                continue
            split_at = fragment.rfind("\n", 0, CHUNK_SIZE + 1)
            if split_at < CHUNK_SIZE // 2:
                split_at = fragment.rfind(" ", 0, CHUNK_SIZE + 1)
            if split_at < CHUNK_SIZE // 2:
                split_at = CHUNK_SIZE
            pending_fragments[0:0] = [
                fragment[:split_at].rstrip(),
                fragment[split_at:].lstrip(),
            ]

        for fragment in fragments:
            current_length = sum(len(part) for part, _ in body) + max(
                len(body) - 1, 0
            ) * 2
            if body and current_length + len(fragment) + 2 > CHUNK_SIZE:
                previous_text = flush_chunk()
                overlap = _overlap_tail(previous_text)
                overlap_pages = tuple(
                    sorted({page for _, block_pages in body for page in block_pages})
                )
                body.clear()
                if overlap:
                    body.append((overlap, overlap_pages))
            body.append((fragment, pages))

    flush_chunk()
    return chunks


def extract_chunks(pdf_path: Path) -> list[Document]:
    with pymupdf.open(pdf_path) as pdf:
        extracted_pages = cast(
            list[dict[str, object]],
            pymupdf4llm.to_markdown(pdf, page_chunks=True),
        )
    pages: list[tuple[int, str]] = []
    following_contents = False
    for page_index, page_chunk in enumerate(extracted_pages):
        text = page_chunk.get("text")
        metadata = page_chunk.get("metadata", {})
        if not isinstance(text, str) or not isinstance(metadata, dict):
            raise TypeError(
                f"Markdown halaman {page_index + 1} dari {pdf_path.name} "
                "memiliki format yang tidak didukung."
            )
        if not text.strip():
            continue
        page_number = metadata.get("page", metadata.get("page_number", page_index + 1))
        if not isinstance(page_number, int):
            raise TypeError(
                f"Nomor halaman {page_index + 1} dari {pdf_path.name} "
                "memiliki tipe yang tidak didukung."
            )
        skip_page = _is_contents_page(text, following_contents)
        if skip_page:
            following_contents = True
            continue
        following_contents = False
        pages.append((page_number, text))

    chunks = _chunks_from_blocks(_page_blocks(pages), pdf_path.name)
    if not chunks:
        raise ValueError(
            f"Tidak ada teks yang dapat diekstrak dari {pdf_path.name}."
        )
    return chunks


def main() -> None:
    pdf_path = find_pdf()
    chunks = extract_chunks(pdf_path)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    existing_ids = collection.get(include=[])["ids"]
    if existing_ids:
        collection.delete(ids=existing_ids)

    vector_store = Chroma(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding_function=get_embeddings(),
    )
    chunk_ids = [
        hashlib.sha256(
            f"{pdf_path.name}:{index}:{chunk.page_content}".encode("utf-8")
        ).hexdigest()
        for index, chunk in enumerate(chunks)
    ]
    vector_store.add_documents(chunks, ids=chunk_ids)
    print(
        f"Indeks berhasil dibuat: {pdf_path.name}, "
        f"{len(chunks)} chunks, {len({chunk.metadata['page'] for chunk in chunks})} halaman berteks."
    )


if __name__ == "__main__":
    main()
