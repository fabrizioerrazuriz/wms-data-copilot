"""Contrato comun para todos los proveedores de LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Precio en USD por 1K tokens (entrada, salida). Fuente: listas publicas de cada proveedor.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.00015, 0.00060),
    "gpt-4o": (0.0025, 0.0100),
    "o4-mini": (0.0011, 0.0044),
    "claude-3-5-haiku-latest": (0.0008, 0.0040),
    "claude-3-5-sonnet-latest": (0.0030, 0.0150),
    "claude-sonnet-4-20250514": (0.0030, 0.0150),
    "llama3.1": (0.0, 0.0),
    "rules-v1": (0.0, 0.0),
}


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int


def estimate_tokens(text: str) -> int:
    """Heuristica (~4 caracteres por token) para proveedores que no reportan usage."""
    return max(1, len(text) // 4)


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    return round((tokens_in / 1000) * price_in + (tokens_out / 1000) * price_out, 6)


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        *,
        system: str,
        user: str,
        task: str = "generic",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...


class LLMError(RuntimeError):
    """Error controlado del proveedor (timeout, cuota, respuesta invalida)."""
