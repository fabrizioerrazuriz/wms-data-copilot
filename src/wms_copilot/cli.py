"""CLI del copiloto: bootstrap, ask, quality, eval, reindex, serve, stats."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from .agent.graph import run_agent
from .agent.tools.quality_tool import analyze_quality
from .config import get_settings
from .db import init_db, table_count
from .evals.runner import markdown_table, run_evals, save_report
from .llm.base import LLMError
from .llm.factory import build_provider, get_llm
from .pipeline.generate_synthetic import generate_all
from .pipeline.load import load_all
from .rag.ingest import ingest_documents
from .rag.retriever import kb_stats
from .telemetry import configure_logging, llm_metrics

TABLES = ["products", "warehouses", "inventory_movements", "purchase_orders", "etl_runs", "kb_chunks", "llm_calls"]


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _provider_from_args(args: argparse.Namespace):
    return build_provider(args.provider) if getattr(args, "provider", None) else get_llm()


def cmd_bootstrap(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.force and settings.data_dir.exists():
        shutil.rmtree(settings.data_dir / "raw", ignore_errors=True)
    paths = generate_all()
    init_db()
    summaries = load_all()
    kb = ingest_documents()
    _print(
        {
            "generated": {name: str(path) for name, path in paths.items()},
            "loads": [asdict(summary) | {"idempotent": summary.idempotent} for summary in summaries],
            "kb": kb,
            "rows": {table: table_count(table) for table in TABLES},
        }
    )
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    result = run_agent(args.question, _provider_from_args(args), intent=args.intent)
    print(result.answer)
    print()
    if result.sql:
        print(f"SQL: {result.sql}")
    if result.citations:
        refs = ", ".join(citation["ref"] for citation in result.citations)
        print(f"Fuentes: {refs}")
    cost = f"US${result.cost_usd:.6f}"
    print(
        f"\n[provider={result.provider} model={result.model} intent={result.intent} "
        f"latency={result.latency_ms}ms cost={cost} request_id={result.request_id}]"
    )
    return 0 if not result.error else 1


def cmd_quality(args: argparse.Namespace) -> int:
    provider = _provider_from_args(args)
    report = analyze_quality(provider, use_llm=args.llm)
    print(report.narrative)
    print()
    for finding in report.findings:
        flag = "OK " if finding.count == 0 else "!! "
        print(f"{flag}{finding.rule_code:<26} [{finding.severity:<6}] {finding.count:>3} · {finding.description}")
    print(f"\nTotal de hallazgos: {report.total_issues}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    report = run_evals(_provider_from_args(args), limit=args.limit)
    print(markdown_table(report))
    path = save_report(report, args.out)
    print(f"\nReporte guardado en {path}")
    if report.pass_rate < args.min_pass:
        print(
            f"\nGATE FALLIDO: pass rate {report.pass_rate:.0%} < minimo {args.min_pass:.0%}",
            file=sys.stderr,
        )
        return 1
    print(f"\nGATE OK: pass rate {report.pass_rate:.0%} >= minimo {args.min_pass:.0%}")
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    _print(ingest_documents())
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("wms_copilot.api.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    _print(
        {
            "rows": {table: table_count(table) for table in TABLES},
            "kb": kb_stats(),
            "llm": llm_metrics(),
            "settings": {
                "db_url": get_settings().db_url.split("@")[-1],
                "llm_provider": get_settings().llm_provider,
                "llm_model": get_settings().resolved_model(),
                "embedding_provider": get_settings().embedding_provider,
                "sql_row_limit": get_settings().sql_row_limit,
                "sql_timeout_s": get_settings().sql_timeout_s,
            },
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wms-copilot", description="Copiloto de datos sobre un WMS")
    sub = parser.add_subparsers(dest="command", required=True)

    bootstrap = sub.add_parser("bootstrap", help="Genera datos sinteticos, carga y indexa la KB")
    bootstrap.add_argument("--force", action="store_true", help="Regenera los CSV desde cero")
    bootstrap.set_defaults(func=cmd_bootstrap)

    ask = sub.add_parser("ask", help="Haz una pregunta al copiloto")
    ask.add_argument("question")
    ask.add_argument("--intent", choices=["sql", "docs", "quality"], default=None)
    ask.add_argument("--provider", choices=["rule-based", "openai", "anthropic", "ollama"], default=None)
    ask.set_defaults(func=cmd_ask)

    quality = sub.add_parser("quality", help="Ejecuta el escaneo de calidad de datos")
    quality.add_argument("--llm", action="store_true", help="Usa el LLM para narrar los hallazgos")
    quality.add_argument("--provider", choices=["rule-based", "openai", "anthropic", "ollama"], default=None)
    quality.set_defaults(func=cmd_quality)

    evaluation = sub.add_parser("eval", help="Corre el golden set y aplica el gate de calidad")
    evaluation.add_argument("--min-pass", type=float, default=0.6, help="Pass rate minimo (0-1)")
    evaluation.add_argument("--limit", type=int, default=None, help="Limita la cantidad de casos")
    evaluation.add_argument("--out", default=None, help="Ruta del reporte JSON")
    evaluation.add_argument("--provider", choices=["rule-based", "openai", "anthropic", "ollama"], default=None)
    evaluation.set_defaults(func=cmd_eval)

    reindex = sub.add_parser("reindex", help="Reindexa la base de conocimiento")
    reindex.set_defaults(func=cmd_reindex)

    serve = sub.add_parser("serve", help="Levanta la API FastAPI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    stats = sub.add_parser("stats", help="Metricas locales de datos, KB y LLM")
    stats.set_defaults(func=cmd_stats)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except LLMError as exc:
        print(f"Error de configuracion del LLM: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
