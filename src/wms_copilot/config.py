"""Configuracion centralizada (12-factor, via variables de entorno)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path | None = None) -> None:
    """.env minimo para no agregar dependencias. No sobreescribe el entorno real."""
    env_path = path or Path(os.environ.get("WMS_ENV_FILE", REPO_ROOT / ".env"))
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Almacenamiento
    db_url: str = "sqlite:///./data/wms.db"
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    artifacts_dir: Path = field(default_factory=lambda: REPO_ROOT / "artifacts")
    kb_dir: Path = field(default_factory=lambda: REPO_ROOT / "kb")

    # LLM
    llm_provider: str = "rule-based"  # rule-based | openai | anthropic | ollama
    llm_model: str = ""
    llm_temperature: float = 0.0
    llm_max_tokens: int = 900

    # Embeddings
    embedding_provider: str = "hashing"  # hashing | openai | ollama
    embedding_model: str = ""
    embedding_dim: int = 256

    # Guardrails SQL
    sql_row_limit: int = 200
    sql_timeout_s: float = 3.0

    # RAG
    rag_top_k: int = 4

    # Observabilidad
    telemetry_enabled: bool = True

    def resolved_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return {
            "rule-based": "rules-v1",
            "openai": "gpt-4o-mini",
            "anthropic": "claude-3-5-haiku-latest",
            "ollama": "llama3.1",
        }.get(self.llm_provider, "rules-v1")

    def resolved_embedding_model(self) -> str:
        if self.embedding_model:
            return self.embedding_model
        return {"hashing": "hash-tf-256", "openai": "text-embedding-3-small", "ollama": "nomic-embed-text"}.get(
            self.embedding_provider, "hash-tf-256"
        )

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.artifacts_dir):
            path.mkdir(parents=True, exist_ok=True)


def _build_settings() -> Settings:
    defaults = Settings()
    return Settings(
        db_url=_env("WMS_DB_URL", defaults.db_url),
        data_dir=Path(_env("WMS_DATA_DIR", str(defaults.data_dir))),
        artifacts_dir=Path(_env("WMS_ARTIFACTS_DIR", str(defaults.artifacts_dir))),
        kb_dir=Path(_env("WMS_KB_DIR", str(defaults.kb_dir))),
        llm_provider=_env("WMS_LLM_PROVIDER", defaults.llm_provider).lower(),
        llm_model=_env("WMS_LLM_MODEL", ""),
        llm_temperature=_env_float("WMS_LLM_TEMPERATURE", defaults.llm_temperature),
        llm_max_tokens=_env_int("WMS_LLM_MAX_TOKENS", defaults.llm_max_tokens),
        embedding_provider=_env("WMS_EMBEDDING_PROVIDER", defaults.embedding_provider).lower(),
        embedding_model=_env("WMS_EMBEDDING_MODEL", ""),
        embedding_dim=_env_int("WMS_EMBEDDING_DIM", defaults.embedding_dim),
        sql_row_limit=_env_int("WMS_SQL_ROW_LIMIT", defaults.sql_row_limit),
        sql_timeout_s=_env_float("WMS_SQL_TIMEOUT_S", defaults.sql_timeout_s),
        rag_top_k=_env_int("WMS_RAG_TOP_K", defaults.rag_top_k),
        telemetry_enabled=_env_bool("WMS_TELEMETRY", defaults.telemetry_enabled),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv()
    settings = _build_settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    """Util para tests que cambian variables de entorno."""
    get_settings.cache_clear()
