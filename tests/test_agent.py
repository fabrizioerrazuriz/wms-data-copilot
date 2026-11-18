"""Agente: routing, tools y telemetria."""

from __future__ import annotations

import pytest

from wms_copilot.agent.graph import classify, run_agent
from wms_copilot.agent.tools.quality_tool import analyze_quality, deterministic_summary
from wms_copilot.agent.tools.sql_tool import SqlGenerationError, text_to_sql
from wms_copilot.llm.rule_based import RuleBasedProvider, rule_based_sql
from wms_copilot.telemetry import llm_metrics


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("¿Haz una auditoría de calidad de datos?", "quality"),
        ("¿Qué significa el campo received_delta?", "docs"),
        ("¿Cuántos movimientos hay por tipo?", "sql"),
        ("¿Qué bodegas tienen saldo negativo?", "sql"),
    ],
)
def test_classify_routes(question: str, expected: str) -> None:
    assert classify(question) == expected


def test_rule_based_generates_valid_sql_for_known_intent() -> None:
    match = rule_based_sql("¿Cuántos movimientos provienen de SAP?")
    assert match is not None
    assert "source_system = 'SAP'" in match[0]


def test_text_to_sql_runs_and_returns_rows(seeded: dict[str, int]) -> None:
    answer = text_to_sql("¿Cuántos movimientos hay por tipo?", RuleBasedProvider())
    assert answer.result.row_count > 0
    assert answer.sql.lower().startswith("select")
    assert "movimientos" in answer.answer


def test_text_to_sql_fails_cleanly_for_unknown_intent(seeded: dict[str, int]) -> None:
    with pytest.raises(SqlGenerationError):
        text_to_sql("¿Cuál es el sentido de la vida?", RuleBasedProvider())


def test_agent_sql_result_is_json_serializable(seeded: dict[str, int]) -> None:
    result = run_agent("¿Cuáles son los productos con más salidas?", intent="sql")
    payload = result.as_dict()
    assert payload["intent"] == "sql"
    assert payload["rows"]
    assert payload["error"] == ""


def test_agent_docs_returns_citations(seeded: dict[str, int]) -> None:
    result = run_agent("¿Qué hago si la carga diaria falla por un duplicado?", intent="docs")
    assert result.citations
    assert result.answer
    assert "[0" in result.answer or "#" in result.answer


def test_quality_scan_detects_injected_anomalies(seeded: dict[str, int]) -> None:
    report = analyze_quality(use_llm=False)
    codes = {finding.rule_code for finding in report.findings if finding.count > 0}
    assert {"NEGATIVE_BALANCE", "INVALID_QUANTITY", "DUPLICATE_MOVEMENT", "OVER_RECEPTION"} <= codes
    assert report.total_issues > 0
    assert "hallazgo" in deterministic_summary(report.findings).lower()


def test_telemetry_records_calls(seeded: dict[str, int]) -> None:
    run_agent("¿Cuál es la capacidad de cada bodega?", intent="sql")
    metrics = llm_metrics()
    assert metrics["totals"]["calls"] >= 1
    assert any(task["task"] == "text_to_sql" for task in metrics["by_task"])
