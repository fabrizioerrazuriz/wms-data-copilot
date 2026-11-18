"""Harness de evaluacion: golden set -> metricas -> reporte -> gate de CI."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..agent.graph import run_agent
from ..agent.tools.sql_tool import MAX_ROWS_PREVIEW
from ..config import get_settings
from ..db import run_select
from ..llm.base import LLMProvider
from ..llm.factory import get_llm
from ..llm.prompts import PROMPT_VERSION
from ..pipeline.schema import schema_sql
from .metrics import numeric_support, percentile, rows_equal

DEFAULT_CASES = Path(__file__).with_name("golden_set.jsonl")


@dataclass
class CaseResult:
    case_id: str
    intent: str
    question: str
    passed: bool
    detail: str
    latency_ms: int = 0
    cost_usd: float = 0.0
    sql: str = ""
    answer: str = ""
    support: float = 1.0


@dataclass
class EvalReport:
    provider: str
    model: str
    cases: list[CaseResult] = field(default_factory=list)
    generated_at: str = ""

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(1 for case in self.cases if case.passed)

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 4) if self.total else 0.0

    @property
    def total_cost(self) -> float:
        return round(sum(case.cost_usd for case in self.cases), 6)

    @property
    def avg_latency(self) -> float:
        return round(sum(case.latency_ms for case in self.cases) / self.total, 1) if self.total else 0.0

    @property
    def p95_latency(self) -> float:
        return percentile([case.latency_ms for case in self.cases], 95)

    @property
    def avg_support(self) -> float:
        sql_cases = [case.support for case in self.cases if case.intent == "sql"]
        return round(sum(sql_cases) / len(sql_cases), 4) if sql_cases else 0.0

    def by_intent(self) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for case in self.cases:
            bucket = summary.setdefault(case.intent, {"total": 0, "passed": 0})
            bucket["total"] += 1
            bucket["passed"] += int(case.passed)
        for bucket in summary.values():
            bucket["pass_rate"] = round(bucket["passed"] / bucket["total"], 4)
        return summary

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "schema_hash": _schema_hash(),
            "totals": {
                "cases": self.total,
                "passed": self.passed,
                "pass_rate": self.pass_rate,
                "avg_latency_ms": self.avg_latency,
                "p95_latency_ms": self.p95_latency,
                "total_cost_usd": self.total_cost,
                "avg_numeric_support": self.avg_support,
            },
            "by_intent": self.by_intent(),
            "cases": [
                {
                    "id": case.case_id,
                    "intent": case.intent,
                    "passed": case.passed,
                    "detail": case.detail,
                    "latency_ms": case.latency_ms,
                    "cost_usd": case.cost_usd,
                    "support": case.support,
                    "sql": case.sql,
                }
                for case in self.cases
            ],
        }




def _schema_hash() -> str:
    return hashlib.sha256(schema_sql().encode("utf-8")).hexdigest()[:12]


def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    source = Path(path or DEFAULT_CASES)
    cases = []
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _sql_case(case: dict[str, Any], provider: LLMProvider) -> CaseResult:
    expected = run_select(case["expected_sql"])
    result = run_agent(case["question"], provider, intent="sql")
    support = (
        numeric_support(
            result.answer,
            result.rows,
            derived=(len(result.rows), len(result.columns), MAX_ROWS_PREVIEW),
        )
        if result.rows
        else 0.0
    )
    if result.error:
        return CaseResult(
            case_id=case["id"],
            intent="sql",
            question=case["question"],
            passed=False,
            detail=f"error: {result.error[:160]}",
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
            sql=result.sql,
            answer=result.answer,
            support=support,
        )
    matched = rows_equal(result.rows, expected.rows)
    detail = "result rows match" if matched else f"rows={len(result.rows)} expected={len(expected.rows)}"
    return CaseResult(
        case_id=case["id"],
        intent="sql",
        question=case["question"],
        passed=matched,
        detail=detail,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
        sql=result.sql,
        answer=result.answer,
        support=support,
    )


def _docs_case(case: dict[str, Any], provider: LLMProvider) -> CaseResult:
    result = run_agent(case["question"], provider, intent="docs")
    cited_docs = {citation["ref"].split("#")[0] for citation in result.citations}
    expected_sources = set(case.get("expect_sources", []))
    retrieval_hit = bool(cited_docs & expected_sources)
    answered = len(result.answer) >= 40 and not result.error
    passed = retrieval_hit and answered
    detail = f"sources={sorted(cited_docs)} expected={sorted(expected_sources)}"
    return CaseResult(
        case_id=case["id"],
        intent="docs",
        question=case["question"],
        passed=passed,
        detail=("retrieval hit" if retrieval_hit else detail) if passed else detail,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
        answer=result.answer,
    )


def _quality_case(case: dict[str, Any], provider: LLMProvider) -> CaseResult:
    result = run_agent(case["question"], provider, intent="quality")
    expected_rule = case["expect_rule"]
    hit = any(f["rule_code"] == expected_rule and f["count"] > 0 for f in result.findings)
    return CaseResult(
        case_id=case["id"],
        intent="quality",
        question=case["question"],
        passed=hit and bool(result.answer),
        detail=f"expect_rule={expected_rule} found={hit}",
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
        answer=result.answer,
    )


def run_evals(provider: LLMProvider | None = None, *, limit: int | None = None, cases_path: Path | None = None) -> EvalReport:
    llm = provider or get_llm()
    cases = load_cases(cases_path)
    if limit:
        cases = cases[:limit]

    report = EvalReport(
        provider=getattr(llm, "name", "unknown"),
        model=getattr(llm, "model", "unknown"),
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    for case in cases:
        intent = case.get("intent", "sql")
        if intent == "sql":
            report.cases.append(_sql_case(case, llm))
        elif intent == "docs":
            report.cases.append(_docs_case(case, llm))
        else:
            report.cases.append(_quality_case(case, llm))
    return report


def markdown_table(report: EvalReport) -> str:
    header = "| # | Caso | Intento | Resultado | ms | Detalle |\n|---|---|---|---|---|---|"
    rows = [
        f"| {index} | {case.case_id} | {case.intent} | {'PASS' if case.passed else 'FAIL'} |"
        f" {case.latency_ms} | {case.detail} |"
        for index, case in enumerate(report.cases, start=1)
    ]
    summary = (
        f"\n**Pass rate:** {report.passed}/{report.total} ({report.pass_rate:.0%}) · "
        f"**Latencia promedio:** {report.avg_latency} ms · **p95:** {report.p95_latency} ms · "
        f"**Costo total:** US${report.total_cost:.6f}"
    )
    return "\n".join([header, *rows, summary])


def save_report(report: EvalReport, path: Path | None = None) -> Path:
    settings = get_settings()
    target = Path(path or settings.artifacts_dir / "eval_report.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return target
