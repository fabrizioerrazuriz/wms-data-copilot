"""RAG: indexacion y recuperacion sobre la base de conocimiento."""

from .ingest import chunk_markdown, ingest_documents
from .retriever import kb_stats, retrieve

__all__ = ["chunk_markdown", "ingest_documents", "kb_stats", "retrieve"]
