"""Validacion estructural e idempotencia de la carga."""

from __future__ import annotations

from pathlib import Path

from wms_copilot.config import get_settings
from wms_copilot.pipeline.generate_synthetic import generate_all
from wms_copilot.pipeline.load import load_all, load_invalid_demo, read_csv
from wms_copilot.pipeline.validators import validate_movements


def _raw() -> Path:
    return get_settings().data_dir / "raw"


def test_generation_is_deterministic() -> None:
    paths = generate_all()
    first = (paths["products"]).read_text(encoding="utf-8")
    generate_all()
    assert (paths["products"]).read_text(encoding="utf-8") == first


def test_validation_rejects_structural_errors(seeded: dict[str, int]) -> None:
    report = validate_movements(
        read_csv(_raw() / "inventory_movements_invalid.csv"),
        product_ids={1, 2, 3},
        warehouse_ids={1, 2, 3, 4},
    )
    assert report.total == 3
    assert report.rejected == 3
    assert report.error_rate == 1.0
    fields = {error.field_name for error in report.errors}
    assert {"product_id", "movement_type", "quantity"} <= fields


def test_invalid_demo_writes_rejected_artifact(seeded: dict[str, int]) -> None:
    summary = load_invalid_demo()
    assert summary["rejected"] == 3
    assert (get_settings().artifacts_dir / "rejected_inventory_movements_invalid.csv").is_file()


def test_load_is_idempotent(seeded: dict[str, int]) -> None:
    first = load_all()
    second = load_all()
    assert all(summary.idempotent for summary in second)
    assert sum(summary.inserted for summary in second) == 0
    assert all(summary.rows_after == summary.rows_before for summary in second)
    assert first[0].rows_after >= 60


def test_business_anomalies_are_not_blocked(seeded: dict[str, int]) -> None:
    """Cantidad <= 0 es anomalia de negocio: entra a la base y la detecta el agente de calidad."""
    report = validate_movements(
        read_csv(_raw() / "inventory_movements.csv"), product_ids=set(range(1, 61)), warehouse_ids={1, 2, 3, 4}
    )
    assert report.rejected == 0
    assert any(row["quantity"] <= 0 for row in report.valid)
