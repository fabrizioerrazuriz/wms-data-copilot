# WMS Data Copilot

[![CI](https://github.com/fabrizioerrazuriz/wms-data-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/fabrizioerrazuriz/wms-data-copilot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Copiloto de datos sobre el pipeline de un WMS (retail): **Text-to-SQL con guardrails**, **RAG sobre
documentación operativa** y **agente de calidad de datos**, con evaluación automatizada y gate en CI.

No es un chatbot de demo: es un sistema con validación de SQL, ejecución de solo lectura, límite de filas,
timeout, redacción de PII, telemetría de costo/latencia y un *golden set* que corre en cada *pull request*.

```
Pregunta: "¿Qué bodegas tienen saldo negativo?"

Combinaciones producto-bodega con saldo negativo.

sku      | warehouse_code | on_hand
---      | ---            | ---
SKU-0043 | CD-01          | -1389
SKU-0038 | CD-04          | -1268
(mostrando 10 de 94 filas)

SQL: SELECT sku, warehouse_code, on_hand FROM v_inventory_balance WHERE on_hand < 0 ORDER BY on_hand ASC
[provider=rule-based model=rules-v1 intent=sql latency=73ms cost=US$0.000000]
```

---

## Resultados medidos (baseline reproducible, sin API key)

`wms-copilot eval` sobre un *golden set* de 20 casos (15 SQL, 3 documentación, 2 calidad):

| Métrica | Valor |
|---|---|
| Pass rate global | **90% (18/20)** |
| Text-to-SQL (match exacto del resultado) | 86,7% (13/15) |
| RAG (documento correcto recuperado) | 100% (3/3) |
| Agente de calidad (regla esperada detectada) | 100% (2/2) |
| Soporte numérico de las respuestas | 86,7% |
| Latencia promedio / p95 | 4,6 ms / 7 ms |
| Costo por corrida (baseline) | US$0,00 |

El 90% corresponde a un **baseline determinista sin LLM** (`rule-based`). Los 2 casos que fallan son
**paráfrasis** (“¿dónde se concentra el mayor volumen de unidades despachadas?”, “¿qué combinaciones
producto-bodega quedaron debiendo stock?”): exactamente el tipo de caso donde un LLM agrega valor y las
reglas no llegan. Con `WMS_LLM_PROVIDER=openai` el mismo comando mide el modelo real y compara contra este
baseline. Ver [docs/evals.md](docs/evals.md).

## Las tres capacidades

**1. Text-to-SQL con guardrails.** El modelo propone SQL; antes de ejecutarse pasa por `sqlglot`:
una sola sentencia, solo `SELECT`/`WITH`, solo tablas del esquema, bloqueo de DDL/DML, `LIMIT` forzado,
timeout de 3 s y transacción que siempre se revierte. Si la consulta viola una regla, hay **un reintento con
el error como feedback**. Las cifras de la respuesta se renderizan desde el resultado, nunca las escribe el
modelo.

**2. RAG con citas.** Ingesta la base de conocimiento (`kb/*.md`: diccionario de datos, runbook de Airflow,
incidentes BOS→WMS→SAP, reglas de calidad), la trocea por secciones, genera embeddings y responde citando
`[documento#chunk]`. Si no hay contexto suficiente, lo dice.

**3. Agente de calidad de datos.** Siete reglas SQL deterministas (saldo negativo, cantidad inválida,
duplicados por documento, sobre-recepción, estado inconsistente, movimiento de producto inactivo, fallas de
ETL) que persisten hallazgos en `data_quality_issues`. El LLM solo narra y prioriza: **nunca calcula**.

## Arquitectura

```
                    ┌──────────────────────────────┐
  pregunta ────────►│  Agent graph (routing)       │
                    │  sql | docs | quality        │
                    └───────┬──────────┬───────────┘
                            │          │
             ┌──────────────▼──┐   ┌───▼────────────────┐   ┌──────────────────┐
             │ sql_tool        │   │ rag_tool           │   │ quality_tool     │
             │ guardrails      │   │ retrieve + cite    │   │ 7 reglas SQL     │
             │ (sqlglot)       │   │                    │   │                  │
             └────────┬────────┘   └───┬────────────────┘   └────────┬─────────┘
                      │                │                             │
             ┌────────▼────────────────▼───────────┐        ┌────────▼─────────┐
             │ LLM provider (intercambiable)        │        │ SQLite / Postgres│
             │ rule-based | openai | anthropic |    │        │ WMS + kb_chunks  │
             │ ollama                               │        │ + llm_calls      │
             └──────────────────────────────────────┘        └──────────────────┘
                      │
             ┌────────▼─────────┐
             │ telemetría       │  tokens, costo, latencia, request_id
             └──────────────────┘
```

Detalle y decisiones en [docs/architecture.md](docs/architecture.md).

## Quickstart (2 minutos, sin API keys)

**Windows, la forma más corta:** doble clic en `iniciar.cmd` (o ejecútalo desde la terminal). Crea el
entorno si falta, genera los datos y abre el navegador en `http://127.0.0.1:8000`, donde puedes escribir la
pregunta directamente.

Para preguntar desde la terminal sin activar el entorno:

```bat
preguntar.cmd "¿Qué bodegas tienen saldo negativo?"
```

**Manual (Windows / Linux / macOS):**

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -e ".[dev]"
python -m wms_copilot.cli bootstrap     # datos sintéticos + carga + índice de la KB
python -m wms_copilot.cli ask "¿Cuántos movimientos hay por tipo?"
python -m wms_copilot.cli quality
python -m wms_copilot.cli eval
```

Con `make`: `make install bootstrap ask Q="¿Qué bodegas tienen saldo negativo?" eval test`.

### API

```bash
python -m wms_copilot.cli serve
# Interfaz web:  http://127.0.0.1:8000
# Documentación: http://127.0.0.1:8000/docs
curl -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" \
  -d '{"question":"¿Cuáles son los productos con más salidas?"}'
```

| Endpoint | Descripción |
|---|---|
| `POST /ask` | Pregunta libre (routing automático) o `intent` forzado |
| `GET /quality/scan` | Ejecuta las 7 reglas y devuelve hallazgos |
| `GET /quality/issues` | Hallazgos abiertos persistidos |
| `GET /metrics/llm` | Llamadas, tokens, costo y latencia por tarea |
| `GET /metrics/quality` | Issues abiertos agrupados por regla |
| `GET /health` | Estado de base, KB y proveedor |

### Docker (PostgreSQL)

```bash
docker compose up --build
# API en http://localhost:8000/docs, Postgres en localhost:5433
```

El esquema es portable: mismo DDL en SQLite (desarrollo) y PostgreSQL (despliegue).

## Conectar un LLM real

```bash
cp .env.example .env
# .env
WMS_LLM_PROVIDER=openai        # openai | anthropic | ollama
WMS_LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...
```

```bash
python -m wms_copilot.cli eval --min-pass 0.85    # mide el modelo real contra el golden set
python -m wms_copilot.cli quality --llm           # narrativa de calidad generada por el modelo
```

Con `WMS_LLM_PROVIDER=ollama` todo corre local: costo cero y los datos no salen de la máquina.

## Modificar datos, documentación o prompt

| Qué quieres cambiar | Edita | Aplica con |
|---|---|---|
| Inventario, órdenes de compra o ETL | `data/raw/*.csv` | `wms-copilot reset --yes` |
| Sumar o restar unidades de un producto | — | `wms-copilot adjust SKU-0003 1000 --warehouse CD-03` |
| Documentación que cita el RAG | `kb/*.md` | `wms-copilot reindex` |
| Modelo o proveedor | `.env` | reinicia `serve` |
| Prompt | `src/wms_copilot/llm/prompts.py` (sube `PROMPT_VERSION`) | `wms-copilot eval` |

`bootstrap` regenera los CSV con el seed y borra tus ediciones; `load` solo inserta lo que falte;
`reset --yes` recarga tus CSV tal como están. Para agregar unidades usa `adjust` (calcula el
`movement_id`, escribe el CSV y lo carga) y no edites el CSV con Excel: Excel cambia el separador a `;`,
reescribe las fechas y puede duplicar claves. Experimentos medidos paso a paso (dato → cifra distinta,
sección nueva → nueva cita) en [docs/experimentos.md](docs/experimentos.md).

## Evaluación y CI

- `src/wms_copilot/evals/golden_set.jsonl`: 20 casos versionados, con dificultad y notas.
- `wms-copilot eval` compara el **resultado de la consulta** con el resultado esperado (multiconjunto, sin
  importar el orden) y calcula soporte numérico de la respuesta, latencia p95 y costo.
- El reporte se guarda en `artifacts/eval_report.json` con `prompt_version` y `schema_hash`: si cambia el
  prompt o el esquema, se ve en el diff.
- GitHub Actions corre lint + 55 tests + bootstrap + escaneo de calidad + **gate de regresión**
  (`--min-pass 0.6`): un PR que empeora al agente rompe el build.

## Decisiones de diseño (y sus costos)

| Decisión | Por qué | Costo asumido |
|---|---|---|
| Routing determinista (no LLM) para clasificar intención | barato, testeable, sin latencia | no generaliza a intenciones nuevas |
| Números renderizados desde el resultado, no escritos por el modelo | elimina alucinación de cifras | respuestas menos “conversacionales” |
| Baseline `rule-based` incluido | CI y demo sin API key; permite comparar | hay que mantener reglas |
| Embeddings JSON + coseno en Python | cero infraestructura, portable | no escala a millones de chunks (ver roadmap: pgvector) |
| Validación estructural separada de reglas de negocio | un ETL que esconde anomalías no sirve | requiere dos capas explícitas |

## Estructura

```
src/wms_copilot/
├── agent/          routing + tools (sql, rag, quality)
├── api/            FastAPI
├── evals/          golden set, métricas, gate
├── llm/            provider abstraction + prompts versionados
├── pipeline/       DDL, generador sintético, validadores, carga idempotente
├── rag/            chunking, embeddings, retriever
├── config.py       settings por entorno
├── db.py           acceso portable SQLite/Postgres
└── telemetry.py    logs estructurados + costo/latencia
dags/               DAG de Airflow (carga → KB → calidad → evals)
kb/                 documentación operativa indexada por el RAG
docs/               arquitectura, evals y ADRs
tests/              55 tests (guardrails, idempotencia, RAG, API, métricas)
```

## Limitaciones conocidas

- Los datos son **sintéticos**: reproducen el esquema y las anomalías de un WMS, no datos de un cliente.
- El retriever es fuerza bruta en memoria: correcto hasta ~10k chunks; después, `pgvector`.
- Sin autenticación en la API: es un proyecto de portafolio, no un servicio expuesto.
- El juez de calidad de las respuestas es heurístico (soporte numérico), no un LLM-as-judge completo.

## Stack

Python 3.12 · FastAPI · SQLAlchemy · sqlglot · SQLite/PostgreSQL · Airflow · Docker · GitHub Actions ·
pytest · ruff · OpenAI / Anthropic / Ollama (intercambiables)

## English summary

A production-minded LLM copilot over a retail WMS pipeline: guarded text-to-SQL (sqlglot allowlist,
read-only execution, row limit, timeout), RAG with citations over operational docs, and a deterministic
data-quality agent. Ships with a versioned golden set, numeric-faithfulness and latency/cost metrics, and a
CI regression gate. Runs fully offline with a rule-based baseline, so tests and CI need no API keys.

---

Autor: **Fabrizio Errázuriz** — Data Engineer / Developer · [github.com/fabrizioerrazuriz](https://github.com/fabrizioerrazuriz)
