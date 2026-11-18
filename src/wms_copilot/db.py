"""Acceso a datos portable (SQLite en desarrollo, PostgreSQL en despliegue)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from .config import get_settings
from .pipeline.schema import schema_sql

_engine: Engine | None = None


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    elapsed_ms: int = 0
    truncated: bool = False

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _sqlite_path(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix) or ":memory:" in url:
        return None
    return Path(url[len(prefix) :])


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.db_url
        connect_args: dict[str, Any] = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            path = _sqlite_path(url)
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, future=True, connect_args=connect_args, pool_pre_ping=True)
    return _engine


def set_engine(engine: Engine | None) -> None:
    """Permite inyectar un engine (tests con SQLite en memoria)."""
    global _engine
    _engine = engine


def dialect() -> str:
    return get_engine().dialect.name


def is_sqlite() -> bool:
    return dialect() == "sqlite"


def split_sql_statements(ddl: str) -> list[str]:
    """Separa DDL en sentencias ignorando comentarios de linea (que pueden contener ';')."""
    cleaned_lines = []
    for line in ddl.splitlines():
        comment_at = line.find("--")
        cleaned_lines.append(line[:comment_at] if comment_at >= 0 else line)
    return [statement.strip() for statement in "\n".join(cleaned_lines).split(";") if statement.strip()]


def init_db(engine: Engine | None = None) -> None:
    """Crea el esquema. Idempotente: el DDL usa IF NOT EXISTS."""
    target = engine or get_engine()
    statements = split_sql_statements(schema_sql())
    with target.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


@contextmanager
def connect() -> Iterator[Connection]:
    engine = get_engine()
    conn = engine.connect()
    try:
        yield conn
    finally:
        conn.close()


def _apply_timeout(conn: Connection, timeout_s: float) -> None:
    """Corta consultas lentas: progress handler en SQLite, statement_timeout en PostgreSQL."""
    deadline = time.monotonic() + max(timeout_s, 0.1)
    if is_sqlite():
        raw = conn.connection.dbapi_connection  # type: ignore[attr-defined]
        if raw is not None and hasattr(raw, "set_progress_handler"):
            raw.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)
    else:
        conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")


def run_select(sql: str, *, row_limit: int | None = None, timeout_s: float | None = None) -> QueryResult:
    """Ejecuta una consulta de solo lectura dentro de una transaccion que siempre se revierte."""
    settings = get_settings()
    limit = row_limit or settings.sql_row_limit
    timeout = timeout_s if timeout_s is not None else settings.sql_timeout_s
    # Se envuelve la consulta para garantizar el tope de filas aunque el SQL no traiga LIMIT.
    guarded = f"SELECT * FROM (\n{sql}\n) AS guarded_result LIMIT {int(limit) + 1}"

    started = time.perf_counter()
    engine = get_engine()
    conn = engine.connect()
    try:
        trans = conn.begin()
        try:
            _apply_timeout(conn, timeout)
            result = conn.execute(text(guarded))
            columns = list(result.keys())
            raw_rows = [tuple(row) for row in result.fetchall()]
        finally:
            trans.rollback()
    finally:
        conn.close()

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    truncated = len(raw_rows) > limit
    return QueryResult(columns=columns, rows=raw_rows[:limit], elapsed_ms=elapsed_ms, truncated=truncated)


def insert_ignore(conn: Connection, table: str, columns: list[str], rows: list[tuple[Any, ...]]) -> int:
    """INSERT que ignora duplicados, portable entre SQLite y PostgreSQL."""
    if not rows:
        return 0
    cols = ", ".join(columns)
    placeholders = ", ".join(f":{c}" for c in columns)
    if is_sqlite():
        statement = f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
        payload = [dict(zip(columns, row, strict=False)) for row in rows]
        result = conn.execute(text(statement), payload)
        return max(result.rowcount or 0, 0)
    statement = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    payload = [dict(zip(columns, row, strict=False)) for row in rows]
    result = conn.execute(text(statement), payload)
    return max(result.rowcount or 0, 0)


def table_count(table: str) -> int:
    with connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
