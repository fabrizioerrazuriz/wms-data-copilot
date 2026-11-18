"""Conocimiento del esquema: allowlist de tablas y resumen para prompts."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files

ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "products",
        "warehouses",
        "inventory_movements",
        "purchase_orders",
        "etl_runs",
        "data_quality_issues",
        "kb_chunks",
        "llm_calls",
        "v_inventory_balance",
        "v_purchase_order_status",
    }
)

# Resumen compacto y estable del modelo: es la unica fuente de verdad que ve el LLM.
SCHEMA_PROMPT = """\
Esquema relacional del WMS (SQLite/PostgreSQL compatible).

products(product_id, sku, name, category, unit_cost, is_active)   -- is_active: 1 activo, 0 inactivo
warehouses(warehouse_id, code, name, city, capacity_units)
inventory_movements(movement_id, movement_type, product_id, warehouse_id, quantity, moved_at, reference_doc, source_system)
    -- movement_type IN suma stock, OUT resta, ADJUST ajusta (+/-), TRANSFER traslada (resta en origen)
    -- quantity siempre > 0; source_system: BOS | SAP | WMS
purchase_orders(po_id, po_number, product_id, warehouse_id, qty_ordered, qty_received, status, ordered_at, source_system)
    -- status: OPEN | PARTIAL | RECEIVED | CANCELLED
etl_runs(run_id, dag_id, task_id, started_at, finished_at, status, rows_processed, error_message)
    -- status: RUNNING | SUCCESS | FAILED

Vistas (usar cuando simplifiquen la consulta):
v_inventory_balance(sku, product_name, category, warehouse_code, on_hand)  -- saldo por producto y bodega
v_purchase_order_status(po_number, status, sku, warehouse_code, qty_ordered, qty_received, received_delta)

Reglas:
- Solo consultas de lectura (SELECT / WITH). Nunca DDL ni DML.
- Filtra siempre por columnas existentes; no inventes tablas ni columnas.
- Para saldos usa v_inventory_balance en lugar de recalcular movimientos.
"""


def schema_sql() -> str:
    """Devuelve el DDL empaquetado con el proyecto."""
    return (files("wms_copilot.pipeline") / "schema.sql").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def declared_tables() -> frozenset[str]:
    """Tablas y vistas declaradas en el DDL (defensa extra contra drift)."""
    names = set(re.findall(r"CREATE\s+(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w]*)", schema_sql(), re.I))
    return frozenset(n.lower() for n in names)


def schema_drift() -> set[str]:
    """Diferencia entre la allowlist y el DDL. Vacio = consistente."""
    return set(declared_tables()) ^ set(ALLOWED_TABLES)
