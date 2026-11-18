"""Tool de Text-to-SQL: generacion, guardrails, ejecucion y respuesta verificable."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from ...config import get_settings
from ...db import QueryResult, is_sqlite, run_select
from ...llm.base import LLMProvider
from ...llm.prompts import render_sql_system, render_sql_user
from ...pii import redact
from ...pipeline.schema import ALLOWED_TABLES, SCHEMA_PROMPT, declared_tables
from ...telemetry import CallTelemetry, get_logger, record_llm_call

logger = get_logger(__name__)

FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Merge,
    exp.Command,
    exp.Grant,
    exp.TruncateTable,
)
ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)
MAX_ROWS_PREVIEW = 10


class SqlGuardError(ValueError):
    """La consulta generada violo una regla de seguridad o de esquema."""


class SqlGenerationError(RuntimeError):
    """El modelo no produjo SQL utilizable."""


@dataclass
class GuardedQuery:
    sql: str
    tables: list[str]


@dataclass
class SqlAnswer:
    question: str
    sql: str
    explanation: str
    result: QueryResult
    answer: str
    attempts: int = 1
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    cost_usd: float = 0.0
    tables: list[str] = field(default_factory=list)


def _sqlglot_dialect() -> str:
    return "sqlite" if is_sqlite() else "postgres"


def validate_sql(sql: str) -> GuardedQuery:
    """Guardrail principal: solo SELECT del esquema permitido, una sola sentencia."""
    cleaned = re.sub(r"```[a-zA-Z]*", "", sql).strip()
    cleaned = cleaned.rstrip(";").strip()
    if not cleaned:
        raise SqlGuardError("La consulta esta vacia.")

    try:
        statements = sqlglot.parse(cleaned, read=_sqlglot_dialect())
    except ParseError as exc:
        raise SqlGuardError(f"No se pudo parsear el SQL: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlGuardError(f"Se esperaba una sola sentencia y se recibieron {len(statements)}.")

    tree = statements[0]
    if not isinstance(tree, ALLOWED_ROOTS):
        raise SqlGuardError(f"Solo se permiten consultas SELECT/WITH. Tipo detectado: {type(tree).__name__}.")
    for node in FORBIDDEN_NODES:
        if tree.find(node) is not None:
            raise SqlGuardError(f"Operacion no permitida: {node.__name__}.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = {table.name.lower() for table in tree.find_all(exp.Table)} - cte_names
    unknown = tables - ALLOWED_TABLES
    if unknown:
        raise SqlGuardError(f"Tablas fuera del esquema permitido: {sorted(unknown)}.")
    if not tables:
        raise SqlGuardError("La consulta no referencia ninguna tabla del WMS.")
    if drift := declared_tables() - ALLOWED_TABLES:
        logger.warning("schema_allowlist_drift", extra={"extra_fields": {"tables": sorted(drift)}})

    return GuardedQuery(sql=cleaned, tables=sorted(tables))


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    candidate = re.sub(r"^```(?:json)?|```$", "", candidate, flags=re.MULTILINE).strip()
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", candidate, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    raise SqlGenerationError(f"Respuesta del modelo no es JSON valido: {text[:200]}")


def _format_cell(value: Any) -> str:
    # Sin separador de miles: la respuesta debe ser copiable a una consulta y parseable por el medidor.
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_answer(explanation: str, result: QueryResult) -> str:
    """La respuesta se arma con los datos reales de la consulta: el modelo nunca escribe las cifras."""
    if result.row_count == 0:
        return f"{explanation} La consulta no devolvio filas."
    header = " | ".join(result.columns)
    separator = " | ".join("---" for _ in result.columns)
    lines = [header, separator]
    for row in result.rows[:MAX_ROWS_PREVIEW]:
        lines.append(" | ".join(_format_cell(value) for value in row))
    table = "\n".join(lines)
    extra = ""
    if result.row_count > MAX_ROWS_PREVIEW:
        extra = f"\n(mostrando {MAX_ROWS_PREVIEW} de {result.row_count} filas)"
    if result.truncated:
        extra += f"\n(resultado truncado al limite de {get_settings().sql_row_limit} filas)"
    return f"{explanation}\n\n{table}{extra}"


def _prompt_for(question: str, repair: str | None = None) -> tuple[str, str]:
    system = render_sql_system(SCHEMA_PROMPT, _sqlglot_dialect())
    user = render_sql_user(question)
    if repair:
        user = f"{user}\n\nCORRECCION REQUERIDA: {repair}\nDevuelve solo el JSON con la consulta corregida."
    return system, user


def text_to_sql(question: str, provider: LLMProvider, *, max_attempts: int = 2) -> SqlAnswer:
    settings = get_settings()
    safe_question, redactions = redact(question)
    if redactions:
        logger.info("pii_redacted", extra={"extra_fields": {"counts": redactions}})

    repair: str | None = None
    total_latency = 0
    total_cost = 0.0
    attempts = 0
    last_error = ""

    while attempts < max_attempts:
        attempts += 1
        system, user = _prompt_for(safe_question, repair)
        response = provider.complete(
            system=system,
            user=user,
            task="text_to_sql",
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
        total_latency += response.latency_ms
        total_cost += response.cost_usd
        record_llm_call(
            CallTelemetry(
                provider=response.provider,
                model=response.model,
                task="text_to_sql",
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                cost_usd=response.cost_usd,
                latency_ms=response.latency_ms,
            )
        )

        try:
            payload = _extract_json(response.text)
            sql = str(payload.get("sql", "")).strip()
            explanation = str(payload.get("explanation", "")).strip()
            if not sql:
                raise SqlGenerationError(str(payload.get("explanation") or "El modelo no genero SQL."))
            guarded = validate_sql(sql)
            result = run_select(guarded.sql)
        except (SqlGuardError, SqlGenerationError) as exc:
            last_error = str(exc)
            logger.warning("sql_attempt_failed", extra={"extra_fields": {"attempt": attempts, "error": last_error}})
            repair = f"La consulta anterior no es valida: {last_error}"
            continue

        return SqlAnswer(
            question=question,
            sql=guarded.sql,
            explanation=explanation or "Resultado de la consulta.",
            result=result,
            answer=render_answer(explanation or "Resultado de la consulta.", result),
            attempts=attempts,
            provider=response.provider,
            model=response.model,
            latency_ms=total_latency,
            cost_usd=round(total_cost, 6),
            tables=guarded.tables,
        )

    raise SqlGenerationError(f"No se pudo generar una consulta valida tras {attempts} intento(s): {last_error}")
