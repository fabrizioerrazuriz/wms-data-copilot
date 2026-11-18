"""Fixtures: base SQLite temporal, datos sinteticos cargados y KB indexada."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _environment(tmp_path_factory: pytest.TempPathFactory) -> None:
    tmp = tmp_path_factory.mktemp("wms_copilot")
    db_path = (tmp / "test.db").as_posix()
    os.environ["WMS_DB_URL"] = f"sqlite:///{db_path}"
    os.environ["WMS_DATA_DIR"] = str(tmp / "data")
    os.environ["WMS_ARTIFACTS_DIR"] = str(tmp / "artifacts")
    os.environ["WMS_KB_DIR"] = str(REPO_ROOT / "kb")
    os.environ["WMS_LLM_PROVIDER"] = "rule-based"
    os.environ["WMS_EMBEDDING_PROVIDER"] = "hashing"
    os.environ["WMS_SQL_ROW_LIMIT"] = "100"
    os.environ["WMS_SQL_TIMEOUT_S"] = "5"

    from wms_copilot.config import reset_settings_cache

    reset_settings_cache()

    from wms_copilot import db

    db.set_engine(None)


@pytest.fixture(scope="session")
def seeded(_environment: None) -> dict[str, int]:
    from wms_copilot.db import table_count
    from wms_copilot.pipeline.generate_synthetic import generate_all
    from wms_copilot.pipeline.load import load_all
    from wms_copilot.rag.ingest import ingest_documents

    generate_all()
    load_all()
    ingest_documents()
    return {
        "products": table_count("products"),
        "movements": table_count("inventory_movements"),
        "chunks": table_count("kb_chunks"),
    }
