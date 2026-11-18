"""Metricas del harness de evaluacion: comparacion de resultados y soporte numerico."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

# No captura digitos dentro de identificadores (SKU-0003, OC-2026005, DOC-99999).
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.\-])(-?\d+(?:[.,]\d+)?)")


def _normalize_cell(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, str):
        return value.strip()
    return value


def normalize_rows(rows: Iterable[Sequence[Any]]) -> list[tuple[Any, ...]]:
    return sorted(tuple(_normalize_cell(v) for v in row) for row in rows)


def rows_equal(actual: Sequence[Sequence[Any]], expected: Sequence[Sequence[Any]]) -> bool:
    """Igualdad como multiconjunto: el orden de las filas no importa, los valores si."""
    if len(actual) != len(expected):
        return False
    return normalize_rows(actual) == normalize_rows(expected)


def _as_float(text: str) -> float | None:
    """Acepta 1234.56, 1.234,56 y 1234,56."""
    cleaned = text.strip()
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def numeric_support(
    answer: str, rows: Sequence[Sequence[Any]], derived: Sequence[float] = ()
) -> float:
    """Proporcion de numeros de la respuesta que existen en los datos consultados.

    Mide 'faithfulness' de forma barata y sin juez externo: si la respuesta cita una cifra
    que no esta en el resultado de la consulta, se considera no soportada.
    `derived` admite cifras que tambien son legitima consecuencia del resultado
    (cardinalidad de filas/columnas, tamano de la ventana de visualizacion).
    """
    numbers = _NUMBER_RE.findall(answer)
    if not numbers:
        return 1.0
    values: set[float] = {round(float(value), 4) for value in derived}
    for row in rows:
        for cell in row:
            if isinstance(cell, (int, float)):
                values.add(round(float(cell), 4))
            elif isinstance(cell, str):
                parsed = _as_float(cell)
                if parsed is not None:
                    values.add(round(parsed, 4))
    if not values:
        return 0.0
    supported = 0
    for raw in numbers:
        parsed = _as_float(raw)
        if parsed is None:
            continue
        if any(abs(parsed - value) < 0.02 for value in values):
            supported += 1
    return supported / len(numbers)


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]
