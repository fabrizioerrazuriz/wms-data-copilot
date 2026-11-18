"""Tool de RAG: responde sobre la documentacion operativa con citas verificables."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...config import get_settings
from ...llm.base import LLMProvider
from ...llm.prompts import render_rag_system, render_rag_user
from ...pii import redact
from ...rag.retriever import RetrievedChunk, retrieve
from ...telemetry import CallTelemetry, get_logger, record_llm_call

logger = get_logger(__name__)


@dataclass
class RagAnswer:
    question: str
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    cost_usd: float = 0.0
    provider: str = ""
    model: str = ""
    top_score: float = 0.0


def answer_from_docs(question: str, provider: LLMProvider, k: int | None = None) -> RagAnswer:
    settings = get_settings()
    safe_question, _ = redact(question)
    chunks: list[RetrievedChunk] = retrieve(safe_question, k=k)

    if not chunks:
        return RagAnswer(
            question=question,
            answer="No encontre documentacion relacionada. Verifica que la base de conocimiento este indexada "
            "(wms-copilot reindex) o reformula la pregunta.",
            provider=provider.name,
            model=provider.model,
        )

    response = provider.complete(
        system=render_rag_system(),
        user=render_rag_user(safe_question, [chunk.as_dict() for chunk in chunks]),
        task="rag_answer",
        temperature=0.2,
        max_tokens=settings.llm_max_tokens,
    )
    record_llm_call(
        CallTelemetry(
            provider=response.provider,
            model=response.model,
            task="rag_answer",
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
        )
    )

    citations = [
        {
            "ref": f"{chunk.doc_id}#{chunk.chunk_index}",
            "title": chunk.title,
            "score": round(chunk.score, 4),
        }
        for chunk in chunks
    ]
    return RagAnswer(
        question=question,
        answer=response.text.strip(),
        citations=citations,
        latency_ms=response.latency_ms,
        cost_usd=response.cost_usd,
        provider=response.provider,
        model=response.model,
        top_score=chunks[0].score,
    )
