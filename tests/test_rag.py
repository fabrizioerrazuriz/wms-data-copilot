"""RAG: chunking, recuperacion y respuesta extractiva."""

from __future__ import annotations

from wms_copilot.agent.tools.rag_tool import answer_from_docs
from wms_copilot.llm.rule_based import RuleBasedProvider
from wms_copilot.rag.embeddings import cosine, hashing_embedding
from wms_copilot.rag.ingest import chunk_markdown, ingest_documents
from wms_copilot.rag.retriever import kb_stats, retrieve


def test_chunking_respects_headings() -> None:
    content = "# Titulo\n\n## Seccion A\n\ntexto de a\n\n## Seccion B\n\ntexto de b\n"
    chunks = chunk_markdown("doc", content)
    titles = [chunk.title for chunk in chunks]
    assert any("Seccion A" in title for title in titles)
    assert any("Seccion B" in title for title in titles)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))


def test_hashing_embeddings_are_deterministic_and_normalized() -> None:
    first = hashing_embedding("saldo negativo en bodega")
    second = hashing_embedding("saldo negativo en bodega")
    assert first == second
    assert abs(cosine(first, second) - 1.0) < 1e-9
    assert 0.0 <= cosine(first, hashing_embedding("traslado entre bodegas")) < 1.0


def test_reindex_is_idempotent(seeded: dict[str, int]) -> None:
    before = kb_stats()
    ingest_documents()
    assert kb_stats() == before


def test_retrieval_finds_the_right_document(seeded: dict[str, int]) -> None:
    chunks = retrieve("¿Qué significa el campo received_delta?", k=3)
    assert chunks
    assert chunks[0].doc_id == "01_diccionario_datos"


def test_rag_answer_includes_citation(seeded: dict[str, int]) -> None:
    answer = answer_from_docs("¿Cómo se reprocesa un lote sin duplicar movimientos?", RuleBasedProvider())
    assert "#" in answer.answer
    assert answer.citations
    assert answer.top_score > 0
