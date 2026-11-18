"""Guardrails de SQL: lo mas importante del proyecto debe estar cubierto por tests."""

from __future__ import annotations

import pytest

from wms_copilot.agent.tools.sql_tool import SqlGuardError, render_answer, validate_sql
from wms_copilot.db import run_select
from wms_copilot.pipeline.schema import schema_drift


def test_schema_allowlist_matches_ddl() -> None:
    assert schema_drift() == set()


def test_accepts_select_and_extracts_tables() -> None:
    guarded = validate_sql("SELECT sku, on_hand FROM v_inventory_balance WHERE on_hand < 0")
    assert guarded.tables == ["v_inventory_balance"]


def test_accepts_cte_without_flagging_cte_alias() -> None:
    guarded = validate_sql(
        "WITH negativos AS (SELECT sku, on_hand FROM v_inventory_balance WHERE on_hand < 0)"
        " SELECT sku FROM negativos"
    )
    assert guarded.tables == ["v_inventory_balance"]


def test_strips_markdown_fences_and_trailing_semicolon() -> None:
    guarded = validate_sql("```sql\nSELECT COUNT(*) FROM products;\n```")
    assert guarded.sql == "SELECT COUNT(*) FROM products"


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM inventory_movements",
        "UPDATE products SET unit_cost = 0",
        "DROP TABLE products",
        "SELECT 1; DROP TABLE products",
        "CREATE TABLE hack (id INTEGER)",
        "SELECT * FROM sqlite_master",
    ],
)
def test_rejects_unsafe_statements(sql: str) -> None:
    with pytest.raises(SqlGuardError):
        validate_sql(sql)


def test_rejects_empty_sql() -> None:
    with pytest.raises(SqlGuardError):
        validate_sql("   ")


def test_row_limit_is_enforced_by_wrapper(seeded: dict[str, int]) -> None:
    result = run_select("SELECT movement_id FROM inventory_movements")
    assert result.row_count <= 100
    assert result.truncated is True


def test_render_answer_reports_empty_result() -> None:
    result = run_select("SELECT sku FROM products WHERE sku = 'NO-EXISTE'")
    text = render_answer("Producto inexistente.", result)
    assert "no devolvio filas" in text
