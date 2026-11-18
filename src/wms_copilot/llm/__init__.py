"""Capa LLM: contrato unico, proveedores intercambiables (incluye baseline sin LLM)."""

from .base import LLMError, LLMProvider, LLMResponse
from .factory import build_provider, get_llm

__all__ = ["LLMError", "LLMProvider", "LLMResponse", "build_provider", "get_llm"]
