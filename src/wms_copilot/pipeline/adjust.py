"""Alta de un movimiento manual sin editar el CSV a mano.

Escribir a mano en `data/raw/inventory_movements.csv` es fragil: hay que calcular el
siguiente `movement_id`, respetar el separador y no duplicar claves. Este modulo hace
eso y deja el movimiento en el CSV (la fuente de verdad del pipeline) para que
`reset` lo reproduzca.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from ..config import get_settings
from ..db import connect
from ..telemetry import get_logger, log_event
from .load import load_all, read_csv
from .validators import MOVEMENT_TYPES

logger = get_logger(__name__)

FIELDS = [
    "movement_id",
    "movement_type",
    "product_id",
    "warehouse_id",
    "quantity",
    "moved_at",
    "reference_doc",
    "source_system",
]


def _delimiter(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig") as handle:
        header = handle.readline()
    return ";" if header.count(";") > header.count(",") else ","


def _next_movement_id(path: Path) -> int:
    csv_ids = [
        int(row["movement_id"])
        for row in read_csv(path)
        if str(row.get("movement_id", "")).strip().isdigit()
    ]
    with connect() as conn:
        db_max = conn.execute(text("SELECT COALESCE(MAX(movement_id), 0) FROM inventory_movements")).scalar_one()
    return max([0, *csv_ids, int(db_max)]) + 1


def add_movement(
    *,
    sku: str,
    quantity: int,
    warehouse: str,
    movement_type: str = "ADJUST",
    reason: str = "",
) -> dict:
    """Agrega un movimiento al CSV, lo carga (sin borrar nada) y devuelve el saldo resultante."""
    settings = get_settings()
    path = settings.data_dir / "raw" / "inventory_movements.csv"
    if not path.is_file():
        raise FileNotFoundError(f"No existe {path}. Ejecuta primero: wms-copilot bootstrap")

    movement_type = movement_type.upper()
    if movement_type not in MOVEMENT_TYPES:
        raise ValueError(f"Tipo invalido '{movement_type}'. Opciones: {sorted(MOVEMENT_TYPES)}")
    if quantity == 0:
        raise ValueError("La cantidad no puede ser 0.")
    if quantity < 0 and movement_type != "ADJUST":
        raise ValueError("Solo ADJUST acepta cantidad negativa; para IN/OUT usa una cantidad positiva.")

    with connect() as conn:
        product = conn.execute(
            text("SELECT product_id FROM products WHERE UPPER(sku) = UPPER(:sku)"), {"sku": sku.strip()}
        ).first()
        location = conn.execute(
            text("SELECT warehouse_id FROM warehouses WHERE UPPER(code) = UPPER(:code)"),
            {"code": warehouse.strip()},
        ).first()
        if product is None:
            raise ValueError(f"No existe el SKU '{sku}'.")
        if location is None:
            raise ValueError(f"No existe la bodega '{warehouse}'.")
        before = conn.execute(
            text(
                "SELECT on_hand FROM v_inventory_balance WHERE sku = :sku AND warehouse_code = :code"
            ),
            {"sku": sku.strip(), "code": warehouse.strip()},
        ).scalar()

    movement_id = _next_movement_id(path)
    row = {
        "movement_id": movement_id,
        "movement_type": movement_type,
        "product_id": int(product[0]),
        "warehouse_id": int(location[0]),
        "quantity": quantity,
        "moved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "reference_doc": reason.strip() or f"AJUSTE-{movement_id}",
        "source_system": "WMS",
    }

    try:
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter=_delimiter(path), lineterminator="\r\n")
            writer.writerow(row)
    except PermissionError as exc:
        raise ValueError(
            f"No se pudo escribir {path.name}: el archivo esta abierto en Excel u otro programa. Cierralo y reintenta."
        ) from exc

    summaries = load_all(ensure_data=False)
    movements = next(summary for summary in summaries if summary.entity == "inventory_movements")
    with connect() as conn:
        persisted = conn.execute(
            text("SELECT 1 FROM inventory_movements WHERE movement_id = :id"), {"id": movement_id}
        ).first()
        if persisted is None:
            raise ValueError(
                "El movimiento no se inserto: revisa artifacts/rejected_inventory_movements.csv "
                f"(movement_id={movement_id})"
            )
        after = conn.execute(
            text(
                "SELECT on_hand FROM v_inventory_balance WHERE sku = :sku AND warehouse_code = :code"
            ),
            {"sku": sku.strip(), "code": warehouse.strip()},
        ).scalar_one()

    log_event(logger, "movement_added", movement_id=movement_id, sku=sku, quantity=quantity)
    return {
        "movement_id": movement_id,
        "sku": sku.strip().upper(),
        "warehouse": warehouse.strip().upper(),
        "movement_type": movement_type,
        "quantity": quantity,
        "inserted": movements.inserted,
        "on_hand_before": int(before) if before is not None else 0,
        "on_hand_after": int(after),
        "reference_doc": row["reference_doc"],
        "csv": str(path),
    }
