"""Seleccion del proveedor de LLM segun configuracion."""

from __future__ import annotations

from functools import lru_cache

from ..config import get_settings
from .api_providers import AnthropicProvider, OllamaProvider, OpenAIProvider
from .base import LLMError, LLMProvider
from .rule_based import RuleBasedProvider

PROVIDERS = ("rule-based", "openai", "anthropic", "ollama")


def build_provider(provider: str | None = None, model: str | None = None) -> LLMProvider:
    settings = get_settings()
    name = (provider or settings.llm_provider).lower()
    resolved_model = model or (settings.resolved_model() if name == settings.llm_provider else "")

    if name == "rule-based":
        return RuleBasedProvider(model=resolved_model or "rules-v1")
    if name == "openai":
        return OpenAIProvider(model=resolved_model or "gpt-4o-mini")
    if name == "anthropic":
        return AnthropicProvider(model=resolved_model or "claude-3-5-haiku-latest")
    if name == "ollama":
        return OllamaProvider(model=resolved_model or "llama3.1")
    raise LLMError(f"Proveedor desconocido '{name}'. Opciones: {', '.join(PROVIDERS)}")


@lru_cache(maxsize=4)
def get_llm(provider: str | None = None) -> LLMProvider:
    return build_provider(provider=provider)


def reset_llm_cache() -> None:
    get_llm.cache_clear()
