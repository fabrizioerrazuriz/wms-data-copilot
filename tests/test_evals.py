"""Harness de evaluacion: metricas y gate."""

from __future__ import annotations

from wms_copilot.evals.metrics import normalize_rows, numeric_support, percentile, rows_equal
from wms_copilot.evals.runner import load_cases, markdown_table, run_evals


def test_rows_equal_is_order_insensitive() -> None:
    assert rows_equal([(1, "a"), (2, "b")], [(2, "b"), (1, "a")])
    assert not rows_equal([(1, "a")], [(1, "b")])
    assert not rows_equal([(1, "a")], [(1, "a"), (2, "b")])


def test_rows_equal_rounds_floats() -> None:
    assert rows_equal([(1.0000001,)], [(1.0,)])


def test_normalize_rows_sorts() -> None:
    assert normalize_rows([(2,), (1,)]) == [(1,), (2,)]


def test_numeric_support_detects_invented_numbers() -> None:
    rows = [(3, "CD-01"), (7, "CD-02")]
    assert numeric_support("Hay 3 filas y 7 unidades", rows) == 1.0
    assert numeric_support("Hay 3 filas y 999 unidades", rows) == 0.5


def test_percentile() -> None:
    assert percentile([10, 20, 30, 40], 95) == 40
    assert percentile([], 95) == 0.0


def test_golden_set_is_loadable_and_covers_three_tools() -> None:
    cases = load_cases()
    intents = {case["intent"] for case in cases}
    assert intents == {"sql", "docs", "quality"}
    assert len(cases) >= 15


def test_eval_run_passes_gate_on_seeded_data(seeded: dict[str, int]) -> None:
    report = run_evals(limit=6)
    assert report.total == 6
    assert report.pass_rate >= 0.6
    assert "PASS" in markdown_table(report)
    payload = report.as_dict()
    assert payload["prompt_version"]
    assert payload["totals"]["cases"] == 6
