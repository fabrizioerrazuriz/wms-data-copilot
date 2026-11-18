"""Cargas idempotentes CSV -> SQL: valida, inserta, verifica y deja evidencia."""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ..config import get_settings
from ..db import connect, get_engine, init_db, insert_ignore, table_count
from ..telemetry import get_logger, log_event
from .generate_synthetic import generate_all
from .validators import (
    ValidationReport,
    validate_etl_runs,
    validate_movements,
    validate_products,
    validate_purchase_orders,
    validate_warehouses,
)

logger = get_logger(__name__)

COLUMNS: dict[str, list[str]] = {
    "products": ["product_id", "sku", "name", "category", "unit_cost", "is_active"],
    "warehouses": ["warehouse_id", "code", "name", "city", "capacity_units"],
    "inventory_movements": [
        "movement_id",
        "movement_type",
        "product_id",
        "warehouse_id",
        "quantity",
        "moved_at",
        "reference_doc",
        "source_system",
    ],
    "purchase_orders": [
        "po_id",
        "po_number",
        "product_id",
        "warehouse_id",
        "qty_ordered",
        "qty_received",
        "status",
        "ordered_at",
        "source_system",
    ],
    "etl_runs": [
        "run_id",
        "dag_id",
        "task_id",
        "started_at",
        "finished_at",
        "status",
        "rows_processed",
        "error_message",
    ],
}


@dataclass
class LoadSummary:
    entity: str
    total: int
    valid: int
    rejected: int
    inserted: int
    rows_before: int
    rows_after: int

    @property
    def idempotent(self) -> bool:
        """True si la carga no creo filas nuevas (reproceso sin duplicados)."""
        return self.rows_after == self.rows_before


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        header = handle.readline()
        handle.seek(0)
        # Excel en es-CL guarda con ";" como separador de lista. Detectarlo evita perder el lote.
        delimiter = ";" if header.count(";") > header.count(",") else ","
        return list(csv.DictReader(handle, delimiter=delimiter))


def _write_rejects(entity: str, report: ValidationReport) -> Path | None:
    if not report.errors:
        return None
    settings = get_settings()
    path = settings.artifacts_dir / f"rejected_{entity}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["row_index", "field", "message"])
        writer.writerows([[e.row_index, e.field_name, e.message] for e in report.errors])
    return path


def _ids(table: str, column: str) -> set[int]:
    with connect() as conn:
        return {int(row[0]) for row in conn.execute(text(f"SELECT {column} FROM {table}")).all()}


def _load(entity: str, report: ValidationReport) -> LoadSummary:
    table = entity
    columns = COLUMNS[entity]
    rows = [tuple(row[column] for column in columns) for row in report.valid]
    before = table_count(table)
    with connect() as conn:
        try:
            inserted = insert_ignore(conn, table, columns, rows)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    after = table_count(table)
    _write_rejects(entity, report)
    summary = LoadSummary(
        entity=entity,
        total=report.total,
        valid=len(report.valid),
        rejected=report.rejected,
        inserted=inserted,
        rows_before=before,
        rows_after=after,
    )
    log_event(logger, "load_completed", **asdict(summary))
    return summary


def _record_run(*, dag_id: str, status: str, rows: int, error: str = "") -> None:
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    with connect() as conn:
        conn.execute(
            text(
                "INSERT INTO etl_runs (run_id, dag_id, task_id, started_at, finished_at, status,"
                " rows_processed, error_message) VALUES (:run_id, :dag_id, :task_id, :started_at,"
                " :finished_at, :status, :rows, :error)"
            ),
            {
                "run_id": f"load-{uuid.uuid4().hex[:8]}",
                "dag_id": dag_id,
                "task_id": "load_csv",
                "started_at": now,
                "finished_at": now,
                "status": status,
                "rows": rows,
                "error": error,
            },
        )
        conn.commit()


def load_all(raw_dir: Path | None = None, *, ensure_data: bool = True) -> list[LoadSummary]:
    """Carga completa. Crea datos sinteticos si no existen."""
    settings = get_settings()
    raw = Path(raw_dir or settings.data_dir / "raw")
    if ensure_data and not (raw / "inventory_movements.csv").is_file():
        generate_all(raw)

    init_db()
    summaries: list[LoadSummary] = []
    total_rows = 0
    try:
        products = _load("products", validate_products(read_csv(raw / "products.csv")))
        warehouses = _load("warehouses", validate_warehouses(read_csv(raw / "warehouses.csv")))
        summaries += [products, warehouses]

        product_ids = _ids("products", "product_id")
        warehouse_ids = _ids("warehouses", "warehouse_id")

        summaries.append(
            _load(
                "inventory_movements",
                validate_movements(read_csv(raw / "inventory_movements.csv"), product_ids, warehouse_ids),
            )
        )
        summaries.append(
            _load(
                "purchase_orders",
                validate_purchase_orders(read_csv(raw / "purchase_orders.csv"), product_ids, warehouse_ids),
            )
        )
        summaries.append(_load("etl_runs", validate_etl_runs(read_csv(raw / "etl_runs.csv"))))
        total_rows = sum(s.inserted for s in summaries)
        _record_run(dag_id="wms_bootstrap", status="SUCCESS", rows=total_rows)
    except Exception as exc:
        _record_run(dag_id="wms_bootstrap", status="FAILED", rows=total_rows, error=str(exc)[:500])
        raise

    return summaries


def _known_ids(raw: Path, table: str, column: str) -> set[int]:
    """IDs de la base o, si esta vacia, del CSV: permite validar antes de borrar."""
    ids = _ids(table, column)
    if ids:
        return ids
    path = raw / f"{table}.csv"
    if not path.is_file():
        return set()
    values = set()
    for row in read_csv(path):
        value = str(row.get(column, "")).strip()
        if value.isdigit():
            values.add(int(value))
    return values


def reset_data(raw_dir: Path | None = None) -> list[LoadSummary]:
    """Borra los datos del WMS (no la KB) y recarga desde los CSV actuales.

    Es la forma correcta de aplicar cambios en los CSV sin regenerarlos con el seed:
    `bootstrap` siempre regenera, `reset` respeta lo que hayas editado a mano.
    """
    settings = get_settings()
    raw = Path(raw_dir or settings.data_dir / "raw")
    movements_path = raw / "inventory_movements.csv"
    if not movements_path.is_file():
        raise FileNotFoundError(f"No hay CSV en {raw}. Ejecuta primero: wms-copilot bootstrap")

    # Guarda contra el accidente tipico: borrar todo y no poder leer ni una fila.
    report = validate_movements(
        read_csv(movements_path),
        _known_ids(raw, "products", "product_id"),
        _known_ids(raw, "warehouses", "warehouse_id"),
    )
    if not report.valid:
        raise ValueError(
            "Ninguna fila de inventory_movements.csv es valida; no se borro nada. "
            f"Revisa el archivo (separador, fechas, ids). Primeros errores: {report.errors[:3]}"
        )

    tables = ["data_quality_issues", "inventory_movements", "purchase_orders", "etl_runs", "products", "warehouses"]
    with get_engine().begin() as conn:
        for table in tables:
            conn.exec_driver_sql(f"DELETE FROM {table}")
    log_event(logger, "data_reset", tables=tables)
    return load_all(raw, ensure_data=False)


def load_invalid_demo(raw_dir: Path | None = None) -> dict[str, Any]:
    """Demuestra que las filas estructuralmente invalidas son rechazadas, no cargadas."""
    settings = get_settings()
    path = Path(raw_dir or settings.data_dir / "raw") / "inventory_movements_invalid.csv"
    report = validate_movements(
        read_csv(path), _ids("products", "product_id"), _ids("warehouses", "warehouse_id")
    )
    _write_rejects("inventory_movements_invalid", report)
    return report.summary()


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps([asdict(s) for s in load_all()], indent=2, ensure_ascii=False))
