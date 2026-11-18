"""Validacion estructural del pipeline.

Separacion deliberada de responsabilidades:
- Aqui se rechazan errores ESTRUCTURALES (tipos, claves foraneas, enums, duplicados de PK).
- Las anomalias de NEGOCIO (cantidad <= 0, sobre-recepcion, saldo negativo) no se bloquean:
  entran a la base y las detecta el agente de calidad. Un ETL que esconde anomalias no sirve.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

MOVEMENT_TYPES = {"IN", "OUT", "ADJUST", "TRANSFER"}
SOURCE_SYSTEMS = {"BOS", "SAP", "WMS"}
PO_STATUSES = {"OPEN", "PARTIAL", "RECEIVED", "CANCELLED"}
ETL_STATUSES = {"RUNNING", "SUCCESS", "FAILED"}


@dataclass(frozen=True)
class ValidationError:
    row_index: int
    field_name: str
    message: str


@dataclass
class ValidationReport:
    entity: str
    total: int
    valid: list[dict[str, Any]] = field(default_factory=list)
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def rejected(self) -> int:
        return len(self.errors)

    @property
    def error_rate(self) -> float:
        return round(self.rejected / self.total, 4) if self.total else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "total": self.total,
            "valid": len(self.valid),
            "rejected": self.rejected,
            "error_rate": self.error_rate,
            "first_errors": [f"fila {e.row_index} {e.field_name}: {e.message}" for e in self.errors[:3]],
        }


def _as_int(row: dict, name: str) -> int:
    value = row.get(name)
    if value is None or str(value).strip() == "":
        raise ValueError("campo obligatorio vacio")
    if isinstance(value, bool):
        raise ValueError("booleano no valido")
    text = str(value).strip()
    if "." in text or "e" in text.lower():
        raise ValueError(f"se esperaba entero, se recibio '{text}'")
    return int(text)


def _as_float(row: dict, name: str) -> float:
    value = str(row.get(name, "")).strip().replace(",", ".")
    if not value:
        raise ValueError("campo obligatorio vacio")
    return float(value)


def _as_timestamp(row: dict, name: str) -> datetime:
    value = str(row.get(name, "")).strip()
    if not value:
        raise ValueError("fecha obligatoria vacia")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"fecha invalida '{value}' (se espera ISO-8601)") from exc


def _as_enum(row: dict, name: str, allowed: set[str]) -> str:
    value = str(row.get(name, "")).strip().upper()
    if value not in allowed:
        raise ValueError(f"valor '{value}' no permitido; opciones: {sorted(allowed)}")
    return value


def _validate(entity: str, rows: list[dict], rules: list[tuple[str, Callable[[dict], Any]]]) -> ValidationReport:
    report = ValidationReport(entity=entity, total=len(rows))
    seen_keys: set[Any] = set()
    for index, row in enumerate(rows):
        clean = dict(row)
        failed = False
        for field_name, rule in rules:
            try:
                clean[field_name] = rule(clean)
            except (ValueError, TypeError) as exc:
                report.errors.append(ValidationError(index, field_name, str(exc)))
                failed = True
                break
        if failed:
            continue
        pk_field = rules[0][0]
        key = clean[pk_field]
        if key in seen_keys:
            report.errors.append(ValidationError(index, pk_field, f"clave duplicada en el lote: {key}"))
            continue
        seen_keys.add(key)
        report.valid.append(clean)
    return report


def validate_products(rows: list[dict]) -> ValidationReport:
    return _validate(
        "products",
        rows,
        [
            ("product_id", lambda r: _as_int(r, "product_id")),
            ("sku", lambda r: str(r.get("sku", "")).strip()),
            ("name", lambda r: str(r.get("name", "")).strip()),
            ("category", lambda r: str(r.get("category", "")).strip() or "Sin categoria"),
            ("unit_cost", lambda r: _as_float(r, "unit_cost")),
            ("is_active", lambda r: _as_int(r, "is_active")),
        ],
    )


def validate_warehouses(rows: list[dict]) -> ValidationReport:
    return _validate(
        "warehouses",
        rows,
        [
            ("warehouse_id", lambda r: _as_int(r, "warehouse_id")),
            ("code", lambda r: str(r.get("code", "")).strip()),
            ("name", lambda r: str(r.get("name", "")).strip()),
            ("city", lambda r: str(r.get("city", "")).strip()),
            ("capacity_units", lambda r: _as_int(r, "capacity_units")),
        ],
    )


def validate_movements(rows: list[dict], product_ids: set[int], warehouse_ids: set[int]) -> ValidationReport:
    def check_product(row: dict) -> int:
        value = _as_int(row, "product_id")
        if value not in product_ids:
            raise ValueError(f"product_id {value} no existe en products")
        return value

    def check_warehouse(row: dict) -> int:
        value = _as_int(row, "warehouse_id")
        if value not in warehouse_ids:
            raise ValueError(f"warehouse_id {value} no existe en warehouses")
        return value

    def check_reference(row: dict) -> str:
        return str(row.get("reference_doc") or "").strip()

    return _validate(
        "inventory_movements",
        rows,
        [
            ("movement_id", lambda r: _as_int(r, "movement_id")),
            ("movement_type", lambda r: _as_enum(r, "movement_type", MOVEMENT_TYPES)),
            ("product_id", check_product),
            ("warehouse_id", check_warehouse),
            ("quantity", lambda r: _as_int(r, "quantity")),
            ("moved_at", lambda r: _as_timestamp(r, "moved_at").isoformat(sep=" ")),
            ("reference_doc", check_reference),
            ("source_system", lambda r: _as_enum(r, "source_system", SOURCE_SYSTEMS)),
        ],
    )


def validate_purchase_orders(
    rows: list[dict], product_ids: set[int], warehouse_ids: set[int]
) -> ValidationReport:
    def check_product(row: dict) -> int:
        value = _as_int(row, "product_id")
        if value not in product_ids:
            raise ValueError(f"product_id {value} no existe en products")
        return value

    def check_warehouse(row: dict) -> int:
        value = _as_int(row, "warehouse_id")
        if value not in warehouse_ids:
            raise ValueError(f"warehouse_id {value} no existe en warehouses")
        return value

    return _validate(
        "purchase_orders",
        rows,
        [
            ("po_id", lambda r: _as_int(r, "po_id")),
            ("po_number", lambda r: str(r.get("po_number", "")).strip()),
            ("product_id", check_product),
            ("warehouse_id", check_warehouse),
            ("qty_ordered", lambda r: _as_int(r, "qty_ordered")),
            ("qty_received", lambda r: _as_int(r, "qty_received")),
            ("status", lambda r: _as_enum(r, "status", PO_STATUSES)),
            ("ordered_at", lambda r: _as_timestamp(r, "ordered_at").isoformat(sep=" ")),
            ("source_system", lambda r: _as_enum(r, "source_system", SOURCE_SYSTEMS)),
        ],
    )


def validate_etl_runs(rows: list[dict]) -> ValidationReport:
    def optional_ts(row: dict, name: str) -> str:
        value = str(row.get(name) or "").strip()
        return _as_timestamp(row, name).isoformat(sep=" ") if value else ""

    return _validate(
        "etl_runs",
        rows,
        [
            ("run_id", lambda r: str(r.get("run_id", "")).strip()),
            ("dag_id", lambda r: str(r.get("dag_id", "")).strip()),
            ("task_id", lambda r: str(r.get("task_id") or "").strip()),
            ("started_at", lambda r: _as_timestamp(r, "started_at").isoformat(sep=" ")),
            ("finished_at", lambda r: optional_ts(r, "finished_at")),
            ("status", lambda r: _as_enum(r, "status", ETL_STATUSES)),
            ("rows_processed", lambda r: _as_int(r, "rows_processed")),
            ("error_message", lambda r: str(r.get("error_message") or "").strip()),
        ],
    )
