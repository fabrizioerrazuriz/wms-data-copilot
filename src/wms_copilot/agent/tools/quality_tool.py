"""Agente de calidad de datos: reglas SQL deterministas + explicacion opcional con LLM."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text

from ...config import get_settings
from ...db import connect, run_select
from ...llm.base import LLMProvider
from ...llm.prompts import render_quality_system, render_quality_user
from ...telemetry import CallTelemetry, get_logger, record_llm_call

logger = get_logger(__name__)


@dataclass(frozen=True)
class QualityRule:
    code: str
    severity: str
    description: str
    sql: str
    impact: str


RULES: list[QualityRule] = [
    QualityRule(
        code="NEGATIVE_BALANCE",
        severity="HIGH",
        description="Productos con saldo negativo en alguna bodega",
        sql=(
            "SELECT sku, warehouse_code, on_hand FROM v_inventory_balance"
            " WHERE on_hand < 0 ORDER BY on_hand ASC"
        ),
        impact="Inventario fisico no conciliable; riesgo de quiebre o sobre-stock en picking.",
    ),
    QualityRule(
        code="INVALID_QUANTITY",
        severity="HIGH",
        description="Movimientos con cantidad cero o negativa",
        sql=(
            "SELECT movement_id, movement_type, quantity, source_system FROM inventory_movements"
            " WHERE quantity <= 0 ORDER BY movement_id"
        ),
        impact="Error de captura que distorsiona el saldo y cualquier conciliacion con SAP.",
    ),
    QualityRule(
        code="DUPLICATE_MOVEMENT",
        severity="MEDIUM",
        description="Movimientos repetidos con la misma referencia, producto, bodega y cantidad",
        sql=(
            "SELECT reference_doc, product_id, warehouse_id, quantity, COUNT(*) AS repeticiones"
            " FROM inventory_movements WHERE reference_doc IS NOT NULL"
            " GROUP BY reference_doc, product_id, warehouse_id, quantity HAVING COUNT(*) > 1"
        ),
        impact="Doble carga de documentos: el stock queda inflado y el reproceso no es idempotente.",
    ),
    QualityRule(
        code="OVER_RECEPTION",
        severity="MEDIUM",
        description="Ordenes de compra recepcionadas por sobre lo ordenado",
        sql=(
            "SELECT po_number, sku, qty_ordered, qty_received FROM v_purchase_order_status"
            " WHERE qty_received > qty_ordered ORDER BY received_delta DESC"
        ),
        impact="Diferencias con el proveedor y con la contabilidad de compras (BOS/SAP).",
    ),
    QualityRule(
        code="STATUS_MISMATCH",
        severity="MEDIUM",
        description="Ordenes marcadas como RECEIVED con cantidad recepcionada distinta a la ordenada",
        sql=(
            "SELECT po_number, qty_ordered, qty_received FROM v_purchase_order_status"
            " WHERE status = 'RECEIVED' AND qty_received <> qty_ordered"
        ),
        impact="Estado inconsistente entre WMS y SAP; bloquea el cierre de recepciones.",
    ),
    QualityRule(
        code="INACTIVE_PRODUCT_MOVEMENT",
        severity="LOW",
        description="Movimientos asociados a productos inactivos",
        sql=(
            "SELECT m.movement_id, p.sku, m.movement_type, m.quantity FROM inventory_movements m"
            " JOIN products p ON p.product_id = m.product_id WHERE p.is_active = 0"
        ),
        impact="Datos maestros desactualizados: el catalogo activo no refleja la operacion real.",
    ),
    QualityRule(
        code="ETL_FAILURES",
        severity="LOW",
        description="Ejecuciones de ETL fallidas registradas",
        sql=(
            "SELECT dag_id, COUNT(*) AS fallas FROM etl_runs WHERE status = 'FAILED'"
            " GROUP BY dag_id ORDER BY fallas DESC"
        ),
        impact="Ventanas de datos incompletas: el reporte diario puede quedar subestimado.",
    ),
]


@dataclass
class RuleFinding:
    rule_code: str
    severity: str
    description: str
    impact: str
    count: int
    sample: str
    rows: list[tuple[Any, ...]] = field(default_factory=list)


def run_quality_scan(*, persist: bool = True) -> list[RuleFinding]:
    """Ejecuta todas las reglas. Cada regla es SQL puro: auditable y reproducible."""
    findings: list[RuleFinding] = []
    for rule in RULES:
        result = run_select(rule.sql, row_limit=50)
        sample = " | ".join(str(value) for value in result.rows[0]) if result.rows else "-"
        findings.append(
            RuleFinding(
                rule_code=rule.code,
                severity=rule.severity,
                description=rule.description,
                impact=rule.impact,
                count=result.row_count,
                sample=sample,
                rows=result.rows[:5],
            )
        )
    if persist:
        _persist(findings)
    return findings


def _persist(findings: list[RuleFinding]) -> None:
    """Idempotente por regla: se reemplazan los hallazgos abiertos de esa regla."""
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    with connect() as conn:
        for finding in findings:
            conn.execute(
                text("DELETE FROM data_quality_issues WHERE rule_code = :code AND resolved = 0"),
                {"code": finding.rule_code},
            )
            if finding.count == 0:
                continue
            conn.execute(
                text(
                    "INSERT INTO data_quality_issues (issue_id, detected_at, rule_code, severity, entity_key,"
                    " detail, resolved) VALUES (:issue_id, :detected_at, :rule_code, :severity, :entity_key,"
                    " :detail, 0)"
                ),
                {
                    "issue_id": uuid.uuid4().hex,
                    "detected_at": now,
                    "rule_code": finding.rule_code,
                    "severity": finding.severity,
                    "entity_key": f"{finding.rule_code}:{finding.sample[:80]}",
                    "detail": f"{finding.description}. Hallazgos: {finding.count}. Ejemplo: {finding.sample}",
                },
            )
        conn.commit()


def deterministic_summary(findings: list[RuleFinding]) -> str:
    active = [f for f in findings if f.count > 0]
    if not active:
        return "Sin hallazgos: las reglas de calidad no detectaron anomalias en la carga actual."
    by_severity: dict[str, int] = {}
    for finding in active:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + finding.count
    parts = [f"{count} hallazgo(s) {severity}" for severity, count in sorted(by_severity.items())]
    worst = sorted(active, key=lambda f: (f.severity != "HIGH", -f.count))[0]
    return (
        f"Se detectaron {' + '.join(parts)} en {len(active)} regla(s). "
        f"El hallazgo mas relevante es {worst.rule_code} ({worst.count}). Impacto: {worst.impact} "
        f"Accion sugerida: reprocesar el lote afectado con carga idempotente y volver a ejecutar el escaneo."
    )


@dataclass
class QualityReport:
    findings: list[RuleFinding]
    narrative: str
    provider: str = ""
    model: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0

    @property
    def total_issues(self) -> int:
        return sum(f.count for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_issues": self.total_issues,
            "narrative": self.narrative,
            "provider": self.provider,
            "model": self.model,
            "cost_usd": self.cost_usd,
            "findings": [
                {
                    "rule_code": f.rule_code,
                    "severity": f.severity,
                    "description": f.description,
                    "impact": f.impact,
                    "count": f.count,
                    "sample": f.sample,
                }
                for f in self.findings
            ],
        }


def analyze_quality(provider: LLMProvider | None = None, *, use_llm: bool = True) -> QualityReport:
    findings = run_quality_scan()
    summary = deterministic_summary(findings)
    if provider is None or not use_llm or provider.name == "rule-based":
        return QualityReport(findings=findings, narrative=summary)

    payload = [
        {
            "rule_code": f.rule_code,
            "severity": f.severity,
            "description": f.description,
            "count": f.count,
            "sample": f.sample,
        }
        for f in findings
        if f.count > 0
    ]
    settings = get_settings()
    response = provider.complete(
        system=render_quality_system(),
        user=render_quality_user(payload),
        task="quality_summary",
        temperature=0.2,
        max_tokens=settings.llm_max_tokens,
    )
    record_llm_call(
        CallTelemetry(
            provider=response.provider,
            model=response.model,
            task="quality_summary",
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
        )
    )
    return QualityReport(
        findings=findings,
        narrative=response.text.strip() or summary,
        provider=response.provider,
        model=response.model,
        cost_usd=response.cost_usd,
        latency_ms=response.latency_ms,
    )
