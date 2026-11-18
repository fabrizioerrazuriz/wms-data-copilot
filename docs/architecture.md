# Arquitectura

## Componentes

| Capa | Módulo | Responsabilidad |
|---|---|---|
| Entrada | `api/main.py`, `cli.py` | FastAPI y CLI sobre el mismo `run_agent` |
| Orquestación | `agent/graph.py` | Clasifica intención y ejecuta el tool correcto |
| Tools | `agent/tools/sql_tool.py` | Text-to-SQL + guardrails + ejecución |
| | `agent/tools/rag_tool.py` | Recuperación y respuesta con citas |
| | `agent/tools/quality_tool.py` | 7 reglas SQL + narrativa |
| LLM | `llm/` | Contrato único, 4 proveedores, prompts versionados |
| Datos | `pipeline/` | DDL portable, validador, carga idempotente |
| Recuperación | `rag/` | Chunking por sección, embeddings, coseno |
| Calidad del sistema | `evals/` | Golden set, métricas, gate de regresión |
| Plataforma | `db.py`, `config.py`, `telemetry.py` | Acceso portable, settings, observabilidad |

## Flujo de una pregunta

1. `POST /ask` → `run_agent(question)`.
2. `classify()` normaliza el texto (sin acentos) y decide `sql | docs | quality` con reglas explícitas.
3. El tool correspondiente llama al proveedor de LLM con un prompt versionado.
4. **Guardrails** sobre la salida del modelo (solo en el tool SQL).
5. Se ejecuta la consulta en una transacción que **siempre se revierte**.
6. La respuesta se renderiza con los datos reales; la telemetría registra tokens, costo, latencia y
   `request_id`.

## Capas de seguridad del Text-to-SQL

1. **Prompt**: instrucción explícita de solo lectura y esquema permitido.
2. **Parser (`sqlglot`)**: una sentencia, raíz `SELECT/WITH`, bloqueo de nodos `INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/MERGE/COMMAND`, allowlist de tablas y vistas (las CTE se excluyen del chequeo).
3. **Ejecución**: envoltura `SELECT * FROM (<sql>) LIMIT n+1`, `statement_timeout` en Postgres o *progress handler* en SQLite, y rollback garantizado.
4. **Redacción de PII** antes de enviar texto al proveedor (email, teléfono, RUT, tarjeta).
5. **Reparación acotada**: un único reintento con el mensaje de error del guardrail; sin bucles infinitos.

Un SQL que falla el guardrail no se ejecuta nunca, y el error queda en el log estructurado.

## Modelo de datos

`products`, `warehouses`, `inventory_movements`, `purchase_orders`, `etl_runs` + tablas de sistema
`data_quality_issues`, `kb_chunks`, `llm_calls`. Vistas `v_inventory_balance` y `v_purchase_order_status`
concentran la lógica de signos y diferencias para que el LLM no la reimplemente (ni la equivoque).

DDL sin `SERIAL`/`IDENTITY` y con IDs `TEXT` en tablas de sistema: el mismo archivo corre en SQLite y
PostgreSQL.

## Idempotencia

- Cada entidad tiene clave natural (`movement_id`, `po_id`, `run_id`).
- `insert_ignore()` usa `INSERT OR IGNORE` en SQLite y `ON CONFLICT DO NOTHING` en Postgres.
- La carga reporta `rows_before`, `inserted`, `rows_after`; un reproceso debe dar `inserted = 0` y
  `rows_after = rows_before`. Eso es exactamente lo que verifica `tests/test_pipeline.py`.
- Si la validación estructural supera el umbral de error, no se carga nada y se registra el fallo en
  `etl_runs`.

## Validación estructural vs. reglas de negocio

| Estructural (bloquea) | Negocio (no bloquea, se reporta) |
|---|---|
| tipo de dato inválido | cantidad ≤ 0 |
| FK inexistente | sobre-recepción |
| enum desconocido | saldo negativo |
| PK duplicada en el lote | estado `RECEIVED` inconsistente |

Un pipeline que rechaza anomalías de negocio esconde el problema; por eso se cargan y las detecta el agente
de calidad.

## Observabilidad

- Logs JSON a stdout con `request_id` en cada línea (`telemetry.py`).
- Tabla `llm_calls`: proveedor, modelo, tarea, tokens, costo, latencia y `ok`.
- `GET /metrics/llm` y `GET /metrics/quality` exponen los agregados.
- `artifacts/eval_report.json` versiona `prompt_version` y `schema_hash` de cada corrida.

## Despliegue

```
Docker Compose:  api (FastAPI) ──► db (Postgres 16)
Airflow:         DAG wms_copilot_daily → carga, reindex, calidad, evals
CI:              lint → tests → bootstrap → calidad → eval gate → build imagen
```

## Escalamiento (roadmap)

| Cuello de botella | Siguiente paso |
|---|---|
| Coseno en memoria sobre `kb_chunks` | `pgvector` con índice HNSW (la imagen `pgvector/pgvector` reemplaza a `postgres`) |
| Sin caché de preguntas | caché semántica por similitud + TTL |
| Un solo proveedor por despliegue | *fallback* en cadena y *circuit breaker* |
| Sin control de cuotas | límite por API key/tenant |
| Sin trazas distribuidas | OpenTelemetry con spans por tool |

## Modos de falla previstos

| Falla | Comportamiento |
|---|---|
| Proveedor LLM caído o sin cuota | `AgentResult.error` con mensaje accionable; la API responde 200 con el error explicado |
| SQL inválido o inseguro | no se ejecuta, se reintenta una vez y se devuelve mensaje de reformulación |
| KB no indexada | respuesta explícita pidiendo `reindex` |
| Base vacía | `/health` en `degraded` con el error |
| Consulta lenta | aborta por timeout sin bloquear el worker |
