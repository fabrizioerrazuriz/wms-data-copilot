"""Recuperacion semantica sobre kb_chunks (busqueda vectorial en memoria, portable)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from ..config import get_settings
from ..db import connect
from .embeddings import cosine, embed_text


@dataclass
class RetrievedChunk:
    doc_id: str
    title: str
    chunk_index: int
    text: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "score": round(self.score, 4),
        }


def load_chunks() -> list[RetrievedChunk]:
    with connect() as conn:
        rows = conn.execute(
            text("SELECT doc_id, title, chunk_index, text, embedding FROM kb_chunks ORDER BY doc_id, chunk_index")
        ).mappings().all()
    return [
        RetrievedChunk(
            doc_id=row["doc_id"],
            title=row["title"],
            chunk_index=int(row["chunk_index"]),
            text=row["text"],
            score=0.0,
        )
        for row in rows
    ]


def retrieve(query: str, k: int | None = None, min_score: float = 0.05) -> list[RetrievedChunk]:
    settings = get_settings()
    top_k = k or settings.rag_top_k
    query_vector = embed_text(query)
    scored: list[RetrievedChunk] = []
    with connect() as conn:
        rows = conn.execute(text("SELECT doc_id, title, chunk_index, text, embedding FROM kb_chunks")).mappings().all()
    for row in rows:
        vector = json.loads(row["embedding"])
        score = cosine(query_vector, vector)
        if score >= min_score:
            scored.append(
                RetrievedChunk(
                    doc_id=row["doc_id"],
                    title=row["title"],
                    chunk_index=int(row["chunk_index"]),
                    text=row["text"],
                    score=score,
                )
            )
    scored.sort(key=lambda chunk: chunk.score, reverse=True)
    return scored[:top_k]


def kb_stats() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) AS chunks, COUNT(DISTINCT doc_id) AS documents FROM kb_chunks")
        ).mappings().one()
    return {"documents": int(row["documents"]), "chunks": int(row["chunks"])}
