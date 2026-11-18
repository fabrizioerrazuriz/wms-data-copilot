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


def test_reset_reloads_from_current_csv(seeded: dict[str, int]) -> None:
    """reset aplica cambios de CSV sin regenerarlos con el seed."""
    from wms_copilot.db import table_count
    from wms_copilot.pipeline.load import reset_data

    before = table_count("inventory_movements")
    summaries = reset_data()
    movements = next(summary for summary in summaries if summary.entity == "inventory_movements")
    assert movements.inserted == before
    assert table_count("inventory_movements") == before


def test_read_csv_accepts_excel_separator_and_dates(tmp_path: Path) -> None:
    """Excel en es-CL guarda con ';' y fechas DD-MM-AAAA: el pipeline debe leerlo igual."""
    path = tmp_path / "movimientos.csv"
    path.write_text(
        "movement_id;movement_type;product_id;warehouse_id;quantity;moved_at;reference_doc;source_system\n"
        "1;IN;3;1;1000;04-11-2026 23:03;DOC-1;BOS\n",
        encoding="utf-8",
    )
    rows = read_csv(path)
    assert rows[0]["movement_type"] == "IN"
    report = validate_movements(rows, {3}, {1})
    assert report.valid[0]["moved_at"] == "2026-11-04 23:03:00"


def test_reset_refuses_to_wipe_when_csv_is_unreadable(seeded: dict[str, int]) -> None:
    """Si el CSV no se puede leer, reset no debe borrar los datos que ya estan cargados."""
    from wms_copilot.db import table_count
    from wms_copilot.pipeline.load import reset_data

    path = _raw() / "inventory_movements.csv"
    original = path.read_bytes()
    before = table_count("inventory_movements")
    try:
        path.write_text("esto no es un csv\n", encoding="utf-8")
        try:
            reset_data()
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("reset_data debio negarse a borrar con un CSV ilegible")
        assert table_count("inventory_movements") == before
    finally:
        path.write_bytes(original)


def test_adjust_adds_movement_and_updates_balance(seeded: dict[str, int]) -> None:
    """El comando adjust agrega unidades sin editar el CSV a mano ni borrar datos."""
    from wms_copilot.pipeline.adjust import add_movement
    from wms_copilot.pipeline.load import reset_data

    path = _raw() / "inventory_movements.csv"
    original = path.read_bytes()
    try:
        result = add_movement(sku="SKU-0003", quantity=1000, warehouse="CD-03", movement_type="IN", reason="test")
        assert result["on_hand_after"] - result["on_hand_before"] == 1000
        assert result["movement_id"] > 0
        assert "test" in path.read_text(encoding="utf-8")
    finally:
        path.write_bytes(original)
        reset_data()


def test_adjust_rejects_negative_quantity_for_receipts(seeded: dict[str, int]) -> None:
    from wms_copilot.pipeline.adjust import add_movement

    try:
        add_movement(sku="SKU-0003", quantity=-10, warehouse="CD-03", movement_type="IN")
    except ValueError as exc:
        assert "negativa" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("un IN con cantidad negativa debe fallar")


def test_business_anomalies_are_not_blocked(seeded: dict[str, int]) -> None:
    """Cantidad <= 0 es anomalia de negocio: entra a la base y la detecta el agente de calidad."""
    report = validate_movements(
        read_csv(_raw() / "inventory_movements.csv"), product_ids=set(range(1, 61)), warehouse_ids={1, 2, 3, 4}
    )
    assert report.rejected == 0
    assert any(row["quantity"] <= 0 for row in report.valid)
