"""Generador de datos sinteticos del WMS (determinista, sin datos reales del cliente).

Incluye anomalias intencionales para que el agente de calidad tenga algo que encontrar:
saldos negativos, cantidades invalidas, movimientos duplicados y sobre-recepciones.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

from ..config import get_settings

SEED = 20261101
ANCHOR = datetime(2026, 11, 1, 8, 0, 0)
DAYS = 14
N_PRODUCTS = 60
CATEGORIES = ["Alimento", "Bebidas", "Limpieza", "Electro", "Textil"]
WAREHOUSES = [
    (1, "CD-01", "Centro Distribucion Santiago", "Santiago", 120_000),
    (2, "CD-02", "Centro Distribucion Valparaiso", "Valparaiso", 45_000),
    (3, "CD-03", "Centro Distribucion Concepcion", "Concepcion", 38_000),
    (4, "CD-04", "Centro Distribucion Antofagasta", "Antofagasta", 22_000),
]
SOURCE_SYSTEMS = [("BOS", 60), ("WMS", 30), ("SAP", 10)]


def _ts(rng: random.Random) -> str:
    return (ANCHOR + timedelta(minutes=rng.randint(0, DAYS * 24 * 60 - 1))).isoformat(sep=" ")


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _products() -> list[dict]:
    rng = random.Random(SEED)
    rows = []
    for product_id in range(1, N_PRODUCTS + 1):
        rows.append(
            {
                "product_id": product_id,
                "sku": f"SKU-{product_id:04d}",
                "name": f"Producto {product_id}",
                "category": CATEGORIES[product_id % len(CATEGORIES)],
                "unit_cost": round(rng.uniform(800, 45_000), 2),
                "is_active": 0 if product_id % 17 == 0 else 1,
            }
        )
    return rows


def _warehouses() -> list[dict]:
    return [
        {
            "warehouse_id": wid,
            "code": code,
            "name": name,
            "city": city,
            "capacity_units": capacity,
        }
        for wid, code, name, city, capacity in WAREHOUSES
    ]


def _movements() -> list[dict]:
    rng = random.Random(SEED + 1)
    types = [("IN", 45), ("OUT", 40), ("ADJUST", 8), ("TRANSFER", 7)]
    type_pool = [t for t, weight in types for _ in range(weight)]
    sources = [s for s, weight in SOURCE_SYSTEMS for _ in range(weight)]

    rows: list[dict] = []
    movement_id = 0
    for _ in range(2400):
        movement_id += 1
        rows.append(
            {
                "movement_id": movement_id,
                "movement_type": rng.choice(type_pool),
                "product_id": rng.randint(1, N_PRODUCTS),
                "warehouse_id": rng.randint(1, len(WAREHOUSES)),
                "quantity": rng.randint(1, 250),
                "moved_at": _ts(rng),
                "reference_doc": f"DOC-{rng.randint(10000, 99999)}",
                "source_system": rng.choice(sources),
            }
        )

    # Anomalia 1: saldo negativo garantizado (producto 7 en CD-02).
    for quantity, when in ((120, "2026-11-02 09:00:00"), (95, "2026-11-03 09:00:00")):
        movement_id += 1
        rows.append(
            {
                "movement_id": movement_id,
                "movement_type": "IN",
                "product_id": 7,
                "warehouse_id": 2,
                "quantity": quantity,
                "moved_at": when,
                "reference_doc": f"DOC-{70000 + quantity}",
                "source_system": "BOS",
            }
        )
    movement_id += 1
    rows.append(
        {
            "movement_id": movement_id,
            "movement_type": "OUT",
            "product_id": 7,
            "warehouse_id": 2,
            "quantity": 900,
            "moved_at": "2026-11-10 16:30:00",
            "reference_doc": "DOC-99999",
            "source_system": "WMS",
        }
    )

    # Anomalia 2: cantidades cero o negativas (error de captura).
    for offset, quantity in enumerate((0, -15, -40)):
        movement_id += 1
        rows.append(
            {
                "movement_id": movement_id,
                "movement_type": "ADJUST",
                "product_id": 12 + offset,
                "warehouse_id": 1,
                "quantity": quantity,
                "moved_at": "2026-11-11 12:00:00",
                "reference_doc": f"DOC-{88100 + offset}",
                "source_system": "SAP",
            }
        )

    # Anomalia 3: movimientos duplicados (doble carga desde SAP).
    for offset in range(4):
        original = rows[500 + offset * 37]
        movement_id += 1
        clone = dict(original)
        clone["movement_id"] = movement_id
        clone["source_system"] = "SAP"
        rows.append(clone)

    # Anomalia 4: movimiento de un producto inactivo.
    movement_id += 1
    rows.append(
        {
            "movement_id": movement_id,
            "movement_type": "OUT",
            "product_id": 17,  # multiple de 17 -> inactivo
            "warehouse_id": 3,
            "quantity": 5,
            "moved_at": "2026-11-12 10:15:00",
            "reference_doc": "DOC-77777",
            "source_system": "BOS",
        }
    )
    return rows


def _purchase_orders() -> list[dict]:
    rng = random.Random(SEED + 2)
    statuses = ["OPEN", "PARTIAL", "RECEIVED", "CANCELLED"]
    rows = []
    for po_id in range(1, 81):
        ordered = rng.randint(20, 600)
        status = rng.choice(statuses)
        received = ordered if status == "RECEIVED" else rng.randint(0, ordered)
        rows.append(
            {
                "po_id": po_id,
                "po_number": f"OC-{2026_000 + po_id}",
                "product_id": rng.randint(1, N_PRODUCTS),
                "warehouse_id": rng.randint(1, len(WAREHOUSES)),
                "qty_ordered": ordered,
                "qty_received": received,
                "status": status,
                "ordered_at": (ANCHOR - timedelta(days=rng.randint(1, 30))).isoformat(sep=" "),
                "source_system": rng.choice(["BOS", "SAP"]),
            }
        )
    # Anomalia 5: sobre-recepcion en 5 ordenes.
    for index in range(5):
        rows[index]["qty_received"] = rows[index]["qty_ordered"] + rng.randint(5, 60)
        rows[index]["status"] = "RECEIVED"
    return rows


def _etl_runs() -> list[dict]:
    rng = random.Random(SEED + 3)
    rows = []
    errors = [
        "pyodbc.IntegrityError: violacion de UNIQUE en inventory_movements.reference_doc",
        "Timeout expired: Oracle query excedio 300s en vw_movimientos",
        "KeyError: columna 'qty_received' ausente en archivo de compras",
        "ConnectionError: no se pudo conectar a SAP RFC en el reintento 3",
    ]
    for index in range(1, 41):
        started = ANCHOR + timedelta(days=index % DAYS, hours=index % 6)
        failed = index % 7 == 0
        rows.append(
            {
                "run_id": f"run-{index:04d}",
                "dag_id": "wms_daily_load" if index % 3 else "wms_inventory_reconcile",
                "task_id": "extract_wms" if index % 2 else "load_sqlserver",
                "started_at": started.isoformat(sep=" "),
                "finished_at": (started + timedelta(minutes=rng.randint(2, 25))).isoformat(sep=" "),
                "status": "FAILED" if failed else "SUCCESS",
                "rows_processed": 0 if failed else rng.randint(5_000, 200_000),
                "error_message": errors[index % len(errors)] if failed else "",
            }
        )
    return rows


def _invalid_movements() -> tuple[list[str], list[dict]]:
    """Filas estructuralmente invalidas: el loader debe rechazarlas, no cargarlas."""
    fields = [
        "movement_id",
        "movement_type",
        "product_id",
        "warehouse_id",
        "quantity",
        "moved_at",
        "reference_doc",
        "source_system",
    ]
    rows = [
        {  # producto inexistente
            "movement_id": 90001,
            "movement_type": "IN",
            "product_id": 9999,
            "warehouse_id": 1,
            "quantity": 10,
            "moved_at": "2026-11-05 10:00:00",
            "reference_doc": "DOC-BAD-1",
            "source_system": "BOS",
        },
        {  # tipo de movimiento desconocido
            "movement_id": 90002,
            "movement_type": "SALIDA_RARA",
            "product_id": 1,
            "warehouse_id": 1,
            "quantity": 10,
            "moved_at": "2026-11-05 10:00:00",
            "reference_doc": "DOC-BAD-2",
            "source_system": "BOS",
        },
        {  # cantidad no numerica
            "movement_id": 90003,
            "movement_type": "IN",
            "product_id": 1,
            "warehouse_id": 1,
            "quantity": "muchos",
            "moved_at": "2026-11-05 10:00:00",
            "reference_doc": "DOC-BAD-3",
            "source_system": "BOS",
        },
    ]
    return fields, rows


def generate_all(output_dir: Path | None = None) -> dict[str, Path]:
    """Escribe los CSV sinteticos. Idempotente: mismo seed, mismos archivos."""
    settings = get_settings()
    target = Path(output_dir or settings.data_dir / "raw")
    target.mkdir(parents=True, exist_ok=True)

    paths = {
        "products": _write_csv(
            target / "products.csv",
            ["product_id", "sku", "name", "category", "unit_cost", "is_active"],
            _products(),
        ),
        "warehouses": _write_csv(
            target / "warehouses.csv",
            ["warehouse_id", "code", "name", "city", "capacity_units"],
            _warehouses(),
        ),
        "inventory_movements": _write_csv(
            target / "inventory_movements.csv",
            [
                "movement_id",
                "movement_type",
                "product_id",
                "warehouse_id",
                "quantity",
                "moved_at",
                "reference_doc",
                "source_system",
            ],
            _movements(),
        ),
        "purchase_orders": _write_csv(
            target / "purchase_orders.csv",
            [
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
            _purchase_orders(),
        ),
        "etl_runs": _write_csv(
            target / "etl_runs.csv",
            [
                "run_id",
                "dag_id",
                "task_id",
                "started_at",
                "finished_at",
                "status",
                "rows_processed",
                "error_message",
            ],
            _etl_runs(),
        ),
    }
    fields, bad_rows = _invalid_movements()
    paths["inventory_movements_invalid"] = _write_csv(
        target / "inventory_movements_invalid.csv", fields, bad_rows
    )
    return paths


if __name__ == "__main__":  # pragma: no cover
    for name, path in generate_all().items():
        print(f"{name}: {path}")
