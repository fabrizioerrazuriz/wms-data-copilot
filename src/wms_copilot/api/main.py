"""API HTTP del copiloto (FastAPI)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from .. import __version__
from ..agent.graph import classify, run_agent
from ..agent.tools.quality_tool import analyze_quality
from ..config import get_settings
from ..db import connect, init_db, table_count
from ..llm.factory import get_llm
from ..rag.retriever import kb_stats
from ..telemetry import configure_logging, get_logger, llm_metrics

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_db()
    logger.info("api_started", extra={"extra_fields": {"version": __version__}})
    yield


app = FastAPI(
    title="WMS Data Copilot",
    version=__version__,
    description=(
        "Copiloto de datos sobre un WMS: Text-to-SQL con guardrails, RAG sobre documentacion "
        "operativa y agente de calidad de datos."
    ),
    lifespan=lifespan,
)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=600, examples=["¿Qué bodegas tienen saldo negativo?"])
    intent: Literal["sql", "docs", "quality"] | None = None


class AskResponse(BaseModel):
    question: str
    intent: str
    answer: str
    sql: str = ""
    columns: list[str] = []
    rows: list[list[Any]] = []
    citations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str = ""
    request_id: str = ""


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    try:
        counts = {
            "products": table_count("products"),
            "inventory_movements": table_count("inventory_movements"),
            "purchase_orders": table_count("purchase_orders"),
        }
        database = "ok"
    except Exception as exc:  # base no inicializada
        counts = {}
        database = f"error: {exc}"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "version": __version__,
        "database": database,
        "rows": counts,
        "kb": kb_stats(),
        "llm": {"provider": settings.llm_provider, "model": settings.resolved_model()},
    }


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest) -> AskResponse:
    try:
        result = run_agent(payload.question, intent=payload.intent)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AskResponse(**result.as_dict())


@app.get("/plan")
def plan(question: str) -> dict[str, str]:
    return {"question": question, "intent": classify(question)}


@app.get("/quality/scan")
def quality_scan() -> dict[str, Any]:
    report = analyze_quality(use_llm=False)
    return report.as_dict()


@app.get("/quality/issues")
def quality_issues(limit: int = 20) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            text(
                "SELECT rule_code, severity, detail, detected_at FROM data_quality_issues"
                " WHERE resolved = 0 ORDER BY detected_at DESC LIMIT :limit"
            ),
            {"limit": limit},
        ).mappings().all()
    return {"issues": [dict(row) for row in rows], "count": len(rows)}


@app.get("/metrics/llm")
def metrics_llm() -> dict[str, Any]:
    return llm_metrics()


@app.get("/metrics/quality")
def metrics_quality() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            text(
                "SELECT rule_code, severity, COUNT(*) AS open_issues FROM data_quality_issues"
                " WHERE resolved = 0 GROUP BY rule_code, severity ORDER BY open_issues DESC"
            )
        ).mappings().all()
    return {"open_issues": [dict(row) for row in rows]}


@app.get("/prompt-version")
def prompt_version() -> dict[str, str]:
    from ..llm.prompts import PROMPT_VERSION

    return {"prompt_version": PROMPT_VERSION, "llm_provider": get_llm().name}
