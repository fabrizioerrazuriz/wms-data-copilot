"""Adaptadores HTTP para proveedores reales. Sin SDKs: menos dependencias, mismo contrato."""

from __future__ import annotations

import os

import httpx

from ..telemetry import Timer
from .base import LLMError, LLMResponse, estimate_cost

DEFAULT_TIMEOUT = 60.0


def _post_json(url: str, *, headers: dict[str, str], payload: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:  # red, DNS, timeout
        raise LLMError(f"Fallo de red al llamar {url}: {exc}") from exc
    if response.status_code >= 400:
        raise LLMError(f"{url} respondio {response.status_code}: {response.text[:300]}")
    return response.json()


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        if not self.api_key:
            raise LLMError(
                "OPENAI_API_KEY no esta definida. Usa WMS_LLM_PROVIDER=rule-based para desarrollo offline."
            )

    def complete(
        self,
        *,
        system: str,
        user: str,
        task: str = "generic",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        timer = Timer()
        payload: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.0 if temperature is None else temperature,
            "max_tokens": max_tokens or 900,
        }
        if task == "text_to_sql":
            payload["response_format"] = {"type": "json_object"}
        data = _post_json(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload=payload,
        )
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Respuesta inesperada de OpenAI: {data}") from exc
        usage = data.get("usage", {})
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
        return LLMResponse(
            text=text,
            model=self.model,
            provider=self.name,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=estimate_cost(self.model, tokens_in, tokens_out),
            latency_ms=timer.elapsed_ms,
        )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str = "claude-3-5-haiku-latest") -> None:
        self.model = model
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY no esta definida. Usa WMS_LLM_PROVIDER=rule-based para desarrollo offline."
            )

    def complete(
        self,
        *,
        system: str,
        user: str,
        task: str = "generic",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        timer = Timer()
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            payload={
                "model": self.model,
                "max_tokens": max_tokens or 900,
                "temperature": 0.0 if temperature is None else temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        try:
            text = "".join(block.get("text", "") for block in data["content"])
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Respuesta inesperada de Anthropic: {data}") from exc
        usage = data.get("usage", {})
        tokens_in = int(usage.get("input_tokens", 0))
        tokens_out = int(usage.get("output_tokens", 0))
        return LLMResponse(
            text=text,
            model=self.model,
            provider=self.name,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=estimate_cost(self.model, tokens_in, tokens_out),
            latency_ms=timer.elapsed_ms,
        )


class OllamaProvider:
    """Modelo local: costo cero y datos que no salen de la maquina."""

    name = "ollama"

    def __init__(self, model: str = "llama3.1") -> None:
        self.model = model
        self.host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

    def complete(
        self,
        *,
        system: str,
        user: str,
        task: str = "generic",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        timer = Timer()
        data = _post_json(
            f"{self.host}/api/chat",
            headers={"content-type": "application/json"},
            payload={
                "model": self.model,
                "stream": False,
                "format": "json" if task == "text_to_sql" else None,
                "options": {"temperature": 0.0 if temperature is None else temperature},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=120.0,
        )
        text = data.get("message", {}).get("content", "")
        tokens_in = int(data.get("prompt_eval_count", 0))
        tokens_out = int(data.get("eval_count", 0))
        return LLMResponse(
            text=text,
            model=self.model,
            provider=self.name,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=0.0,
            latency_ms=timer.elapsed_ms,
        )
