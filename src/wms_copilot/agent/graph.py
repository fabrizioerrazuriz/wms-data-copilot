"""Grafo del agente: planificar -> ejecutar tool -> responder.

Routing determinista a proposito: la clasificacion de intencion es barata, testeable
y auditable. El LLM se usa donde aporta (SQL, redaccion), no para decidir el flujo.
Ver docs/adr/0001.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from ..config import get_settings
from ..llm.base import LLMError, LLMProvider
from ..llm.factory import get_llm
from ..telemetry import Timer, get_logger, set_request_id
from .tools.quality_tool import QualityReport, analyze_quality
from .tools.rag_tool import RagAnswer, answer_from_docs
from .tools.sql_tool import SqlAnswer, SqlGenerationError, SqlGuardError, text_to_sql

logger = get_logger(__name__)

Intent = Literal["sql", "docs", "quality"]
VALID_INTENTS: tuple[Intent, ...] = ("sql", "docs", "quality")

QUALITY_HINTS = (
    "calidad de datos",
    "calidad del dato",
    "auditoria",
    "audita",
    "anomalia",
    "anomalias",
    "inconsistencia",
    "problemas de datos",
    "escaneo de calidad",
)
DOCS_HINTS = (
    "que significa",
    "como funciona",
    "como se reprocesa",
    "como reproceso",
    "runbook",
    "documentacion",
    "por que fallo",
    "por que fallan",
    "que hago si",
    "politica",
    "definicion",
    "diccionario",
    "segun la documentacion",
    "procedimiento",
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def classify(question: str) -> Intent:
    q = _normalize(question)
    if any(hint in q for hint in QUALITY_HINTS):
        return "quality"
    if any(hint in q for hint in DOCS_HINTS):
        return "docs"
    return "sql"


@dataclass
class AgentResult:
    question: str
    intent: Intent
    answer: str
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str = ""
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    request_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "intent": self.intent,
            "answer": self.answer,
            "sql": self.sql,
            "columns": self.columns,
            "rows": [list(row) for row in self.rows],
            "citations": self.citations,
            "findings": self.findings,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "error": self.error,
            "request_id": self.request_id,
        }


def run_agent(question: str, provider: LLMProvider | None = None, *, intent: Intent | None = None) -> AgentResult:
    """Punto de entrada unico del copiloto."""
    if not question or not question.strip():
        raise ValueError("La pregunta no puede estar vacia.")

    request_id = set_request_id()
    timer = Timer()
    plan: Intent = intent or classify(question)
    llm = provider or get_llm()

    try:
        if plan == "sql":
            sql_answer: SqlAnswer = text_to_sql(question, llm)
            return AgentResult(
                question=question,
                intent=plan,
                answer=sql_answer.answer,
                provider=sql_answer.provider,
                model=sql_answer.model,
                latency_ms=timer.elapsed_ms,
                cost_usd=sql_answer.cost_usd,
                sql=sql_answer.sql,
                columns=sql_answer.result.columns,
                rows=sql_answer.result.rows,
                request_id=request_id,
            )

        if plan == "docs":
            rag_answer: RagAnswer = answer_from_docs(question, llm)
            return AgentResult(
                question=question,
                intent=plan,
                answer=rag_answer.answer,
                provider=rag_answer.provider,
                model=rag_answer.model,
                latency_ms=timer.elapsed_ms,
                cost_usd=rag_answer.cost_usd,
                citations=rag_answer.citations,
                request_id=request_id,
            )

        report: QualityReport = analyze_quality(llm)
        return AgentResult(
            question=question,
            intent=plan,
            answer=report.narrative,
            provider=report.provider or llm.name,
            model=report.model or llm.model,
            latency_ms=timer.elapsed_ms,
            cost_usd=report.cost_usd,
            findings=[finding for finding in report.as_dict()["findings"]],
            request_id=request_id,
        )
    except (SqlGuardError, SqlGenerationError) as exc:
        logger.warning("agent_sql_failed", extra={"extra_fields": {"error": str(exc), "intent": plan}})
        return AgentResult(
            question=question,
            intent=plan,
            answer=(
                "No pude responder con una consulta segura. Intenta reformular la pregunta indicando "
                "producto (SKU), bodega o periodo."
            ),
            provider=llm.name,
            model=llm.model,
            latency_ms=timer.elapsed_ms,
            error=str(exc),
            request_id=request_id,
        )
    except LLMError as exc:
        logger.error("agent_llm_error", extra={"extra_fields": {"error": str(exc)}})
        return AgentResult(
            question=question,
            intent=plan,
            answer=f"El proveedor de LLM no esta disponible: {exc}",
            provider=llm.name,
            model=llm.model,
            latency_ms=timer.elapsed_ms,
            error=str(exc),
            request_id=request_id,
        )


def plan_only(question: str) -> dict[str, Any]:
    settings = get_settings()
    return {
        "question": question,
        "intent": classify(question),
        "provider": settings.llm_provider,
        "model": settings.resolved_model(),
    }
