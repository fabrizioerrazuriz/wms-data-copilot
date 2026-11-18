"""API HTTP end-to-end con TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient

from wms_copilot.api.main import app


def test_health_reports_database_and_kb(seeded: dict[str, int]) -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert body["rows"]["products"] >= 60
    assert body["kb"]["documents"] >= 4


def test_ask_sql_returns_rows_and_sql(seeded: dict[str, int]) -> None:
    with TestClient(app) as client:
        response = client.post("/ask", json={"question": "¿Cuántos movimientos hay por tipo?"})
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "sql"
    assert body["sql"]
    assert body["rows"]
    assert body["provider"] == "rule-based"


def test_ask_docs_returns_citations(seeded: dict[str, int]) -> None:
    with TestClient(app) as client:
        response = client.post("/ask", json={"question": "¿Qué significa el campo received_delta?"})
    body = response.json()
    assert body["intent"] == "docs"
    assert body["citations"]


def test_ask_validates_short_question(seeded: dict[str, int]) -> None:
    with TestClient(app) as client:
        response = client.post("/ask", json={"question": "ok"})
    assert response.status_code == 422


def test_quality_endpoints(seeded: dict[str, int]) -> None:
    with TestClient(app) as client:
        scan = client.get("/quality/scan")
        issues = client.get("/quality/issues")
        metrics = client.get("/metrics/quality")
    assert scan.status_code == 200
    assert scan.json()["total_issues"] > 0
    assert issues.status_code == 200
    assert metrics.json()["open_issues"]


def test_plan_endpoint(seeded: dict[str, int]) -> None:
    with TestClient(app) as client:
        response = client.get("/plan", params={"question": "Haz una auditoría de calidad"})
    assert response.json()["intent"] == "quality"
