"""Observabilidad: logs estructurados, telemetria de llamadas LLM y metricas."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from .config import get_settings
from .db import connect

_request_id: ContextVar[str] = ContextVar("request_id", default="")
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if request_id := current_request_id():
            payload["request_id"] = request_id
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("uvicorn.access").setLevel("WARNING")
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def set_request_id(value: str | None = None) -> str:
    rid = value or new_request_id()
    _request_id.set(rid)
    return rid


def current_request_id() -> str:
    return _request_id.get()


def log_event(logger: logging.Logger, msg: str, **fields: Any) -> None:
    logger.info(msg, extra={"extra_fields": fields})


@dataclass
class CallTelemetry:
    provider: str
    model: str
    task: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    ok: bool = True


def record_llm_call(telemetry: CallTelemetry, request_id: str | None = None) -> None:
    """Persiste la llamada. Nunca rompe el flujo principal si la BD no esta lista."""
    if not get_settings().telemetry_enabled:
        return
    try:
        with connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO llm_calls (call_id, request_id, provider, model, task, tokens_in, tokens_out,"
                    " cost_usd, latency_ms, ok, created_at) VALUES (:call_id, :request_id, :provider, :model,"
                    " :task, :tokens_in, :tokens_out, :cost_usd, :latency_ms, :ok, :created_at)"
                ),
                {
                    "call_id": uuid.uuid4().hex,
                    "request_id": request_id or current_request_id() or None,
                    "provider": telemetry.provider,
                    "model": telemetry.model,
                    "task": telemetry.task,
                    "tokens_in": telemetry.tokens_in,
                    "tokens_out": telemetry.tokens_out,
                    "cost_usd": telemetry.cost_usd,
                    "latency_ms": telemetry.latency_ms,
                    "ok": 1 if telemetry.ok else 0,
                    "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
            )
            conn.commit()
    except Exception:  # pragma: no cover - la telemetria es best-effort
        get_logger(__name__).debug("telemetry_insert_failed", exc_info=True)


def llm_metrics() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(tokens_in),0) AS tokens_in,"
                " COALESCE(SUM(tokens_out),0) AS tokens_out, COALESCE(SUM(cost_usd),0) AS cost_usd,"
                " COALESCE(AVG(latency_ms),0) AS avg_latency_ms FROM llm_calls"
            )
        ).mappings().one()
        by_task = conn.execute(
            text(
                "SELECT task, COUNT(*) AS calls, ROUND(AVG(latency_ms),1) AS avg_latency_ms,"
                " ROUND(SUM(cost_usd),6) AS cost_usd FROM llm_calls GROUP BY task ORDER BY calls DESC"
            )
        ).mappings().all()
        failures = conn.execute(text("SELECT COUNT(*) FROM llm_calls WHERE ok = 0")).scalar_one()
    return {
        "totals": {
            "calls": int(row["calls"]),
            "tokens_in": int(row["tokens_in"]),
            "tokens_out": int(row["tokens_out"]),
            "cost_usd": round(float(row["cost_usd"]), 6),
            "avg_latency_ms": round(float(row["avg_latency_ms"]), 1),
            "failures": int(failures),
        },
        "by_task": [dict(r) for r in by_task],
    }


class Timer:
    def __init__(self) -> None:
        self._start = time.perf_counter()

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)
