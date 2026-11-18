"""DAG de Airflow: carga diaria, reindexacion de la KB, calidad y evaluacion.

Se ejecuta en el scheduler de Airflow (fuera de la API). Los imports del paquete son
locales para que el parseo del DAG sea liviano.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

DEFAULT_ARGS = {
    "owner": "data-platform",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "sla": timedelta(minutes=45),
}


def _load_wms(**_: Any) -> dict[str, Any]:
    from dataclasses import asdict

    from wms_copilot.pipeline.generate_synthetic import generate_all
    from wms_copilot.pipeline.load import load_all

    generate_all()
    summaries = load_all()
    return {"loads": [asdict(summary) for summary in summaries]}


def _reindex_kb(**_: Any) -> dict[str, Any]:
    from wms_copilot.rag.ingest import ingest_documents

    return ingest_documents()


def _quality_scan(**_: Any) -> dict[str, Any]:
    from wms_copilot.agent.tools.quality_tool import analyze_quality

    report = analyze_quality(use_llm=False)
    if report.total_issues == 0:
        return {"total_issues": 0}
    return {"total_issues": report.total_issues, "narrative": report.narrative}


def _run_evals(**_: Any) -> dict[str, Any]:
    from wms_copilot.evals.runner import run_evals, save_report

    report = run_evals()
    save_report(report)
    if report.pass_rate < 0.6:
        raise ValueError(f"Gate de evaluacion fallido: pass rate {report.pass_rate:.0%} < 60%")
    return report.as_dict()["totals"]


with DAG(
    dag_id="wms_copilot_daily",
    description="Carga WMS, reindexa la KB del copiloto, escanea calidad y evalua el agente",
    start_date=datetime(2026, 11, 1),
    schedule="0 2 * * *",
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["wms", "data-quality", "llm"],
) as dag:
    load_wms = PythonOperator(task_id="load_wms", python_callable=_load_wms)
    reindex_kb = PythonOperator(task_id="reindex_kb", python_callable=_reindex_kb)
    quality_scan = PythonOperator(task_id="quality_scan", python_callable=_quality_scan)
    run_evals_task = PythonOperator(task_id="run_evals", python_callable=_run_evals)
    publish_metrics = BashOperator(
        task_id="publish_metrics",
        bash_command="wms-copilot stats > /tmp/wms_copilot_stats.json && cat /tmp/wms_copilot_stats.json",
    )

    load_wms >> reindex_kb >> quality_scan >> run_evals_task >> publish_metrics
