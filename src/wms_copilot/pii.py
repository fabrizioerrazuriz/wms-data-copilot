"""Redaccion de datos personales antes de enviar texto a un proveedor externo."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
    ("PHONE", re.compile(r"(?<![\d.])(?:\+?56\s?9\s?|\+?56\s?2\s?)?\d{4}[\s-]?\d{4}(?![\d.])")),
    ("RUT", re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-[\dkK]\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
]


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Devuelve el texto con PII enmascarada y el conteo por tipo."""
    counts: dict[str, int] = {}
    redacted = text
    for label, pattern in _PATTERNS:
        redacted, hits = pattern.subn(f"[{label}_REDACTED]", redacted)
        if hits:
            counts[label] = hits
    return redacted, counts
