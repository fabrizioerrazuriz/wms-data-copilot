"""Ingesta de la base de conocimiento: markdown -> chunks -> embeddings -> tabla kb_chunks."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ..config import get_settings
from ..db import connect, init_db
from ..telemetry import get_logger, log_event
from .embeddings import embed_texts

logger = get_logger(__name__)

MAX_CHARS = 900
OVERLAP = 120
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.M)


@dataclass
class Chunk:
    doc_id: str
    title: str
    chunk_index: int
    text: str


def _split_long(text_block: str) -> list[str]:
    if len(text_block) <= MAX_CHARS:
        return [text_block.strip()]
    pieces: list[str] = []
    start = 0
    while start < len(text_block):
        end = min(start + MAX_CHARS, len(text_block))
        if end < len(text_block):
            minimum = start + MAX_CHARS // 2
            # Corte preferente: parrafo, luego oracion, luego linea. Nunca a mitad de palabra si se puede evitar.
            for separator in ("\n\n", ". ", "\n", " "):
                cut = text_block.rfind(separator, minimum, end)
                if cut > minimum:
                    end = cut + (1 if separator == ". " else 0)
                    break
        pieces.append(text_block[start:end].strip())
        start = max(end - OVERLAP, start + 1)
    return [p for p in pieces if p]


def chunk_markdown(doc_id: str, content: str) -> list[Chunk]:
    """Chunking por seccion (heading), respetando estructura semantica del documento."""
    blocks: list[tuple[str, str]] = []
    matches = list(_HEADING_RE.finditer(content))
    if not matches:
        blocks.append((doc_id, content))
    else:
        for index, match in enumerate(matches):
            title = match.group(2).strip()
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            blocks.append((f"{doc_id} · {title}", content[start:end]))

    chunks: list[Chunk] = []
    for title, body in blocks:
        for piece in _split_long(body):
            chunks.append(Chunk(doc_id=doc_id, title=title, chunk_index=len(chunks), text=piece))
    return chunks


def ingest_documents(kb_dir: Path | None = None, *, replace: bool = True) -> dict[str, Any]:
    """Reindexa la KB. Idempotente: borra los chunks del documento antes de insertarlos."""
    settings = get_settings()
    init_db()
    source = Path(kb_dir or settings.kb_dir)
    files = sorted(source.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"No hay documentos .md en {source}")

    total_chunks = 0
    docs: list[str] = []
    for path in files:
        doc_id = path.stem
        chunks = chunk_markdown(doc_id, path.read_text(encoding="utf-8"))
        if not chunks:
            continue
        vectors = embed_texts([f"{chunk.title}\n{chunk.text}" for chunk in chunks])
        rows = [
            {
                "chunk_id": uuid.uuid4().hex,
                "doc_id": chunk.doc_id,
                "title": chunk.title[:200],
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "embedding": json.dumps(vector),
                "updated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            }
            for chunk, vector in zip(chunks, vectors, strict=False)
        ]
        with connect() as conn:
            if replace:
                conn.execute(text("DELETE FROM kb_chunks WHERE doc_id = :doc_id"), {"doc_id": doc_id})
            conn.execute(
                text(
                    "INSERT INTO kb_chunks (chunk_id, doc_id, title, chunk_index, text, embedding, updated_at)"
                    " VALUES (:chunk_id, :doc_id, :title, :chunk_index, :text, :embedding, :updated_at)"
                ),
                rows,
            )
            conn.commit()
        total_chunks += len(rows)
        docs.append(doc_id)
        log_event(logger, "kb_doc_indexed", doc_id=doc_id, chunks=len(rows))

    return {"documents": docs, "chunks": total_chunks, "embedding_provider": settings.embedding_provider}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(ingest_documents(), indent=2, ensure_ascii=False))
