"""Embeddings: hashing determinista (offline), OpenAI u Ollama. Similitud coseno en Python."""

from __future__ import annotations

import hashlib
import math
import os
import re
from functools import lru_cache

import httpx

from ..config import get_settings
from ..llm.base import LLMError

_TOKEN_RE = re.compile(r"[a-z0-9áéíóúñ]+")


def _tokens(text: str) -> list[str]:
    words = _TOKEN_RE.findall(text.lower())
    # Palabras + bigramas de caracteres: captura algo de morfologia sin dependencias.
    grams = [w[i : i + 4] for w in words if len(w) > 4 for i in range(len(w) - 3)]
    return words + grams


def _bucket(token: str, dim: int) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") % dim


def hashing_embedding(text: str, dim: int | None = None) -> list[float]:
    size = dim or get_settings().embedding_dim
    vector = [0.0] * size
    for token in _tokens(text):
        vector[_bucket(token, size)] += 1.0
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def embed_texts(texts: list[str]) -> list[list[float]]:
    provider = get_settings().embedding_provider
    if provider == "hashing":
        return [hashing_embedding(text) for text in texts]
    if provider == "openai":
        return _openai_embeddings(texts)
    if provider == "ollama":
        return [_ollama_embedding(text) for text in texts]
    raise LLMError(f"Proveedor de embeddings desconocido: {provider}")


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


@lru_cache(maxsize=1)
def _openai_client() -> httpx.Client:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise LLMError("OPENAI_API_KEY no definida: usa WMS_EMBEDDING_PROVIDER=hashing")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    return httpx.Client(base_url=base, headers={"Authorization": f"Bearer {api_key}"}, timeout=60.0)


def _openai_embeddings(texts: list[str]) -> list[list[float]]:
    client = _openai_client()
    payload = {"model": get_settings().resolved_embedding_model(), "input": texts}
    response = client.post("/embeddings", json=payload)
    if response.status_code >= 400:
        raise LLMError(f"OpenAI embeddings {response.status_code}: {response.text[:200]}")
    data = response.json()["data"]
    return [item["embedding"] for item in data]


def _ollama_embedding(text: str) -> list[float]:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    payload = {"model": get_settings().resolved_embedding_model(), "prompt": text}
    response = httpx.post(f"{host}/api/embeddings", json=payload, timeout=120.0)
    if response.status_code >= 400:
        raise LLMError(f"Ollama embeddings {response.status_code}: {response.text[:200]}")
    return response.json()["embedding"]
