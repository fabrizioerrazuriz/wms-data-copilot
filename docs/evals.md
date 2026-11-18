# Evaluación del copiloto

Un sistema con LLM sin evaluación es una demo. Este repositorio mide tres capacidades con un *golden set*
versionado y bloquea el merge si la calidad cae.

## Cómo correr

```bash
python -m wms_copilot.cli eval                      # baseline offline (rule-based)
python -m wms_copilot.cli eval --min-pass 0.85      # gate más estricto
python -m wms_copilot.cli eval --provider openai    # mide el modelo real
python -m wms_copilot.cli eval --limit 5            # humo rápido
```

El reporte se escribe en `artifacts/eval_report.json`. En CI, `--min-pass 0.6` es el piso; el número real se
revisa en el artefacto.

## Formato del golden set

`src/wms_copilot/evals/golden_set.jsonl`, un caso por línea:

```json
{"id":"sql-01","intent":"sql","question":"...","expected_sql":"SELECT ...","difficulty":"easy"}
{"id":"doc-01","intent":"docs","question":"...","expect_sources":["02_runbook_airflow"]}
{"id":"quality-01","intent":"quality","question":"...","expect_rule":"NEGATIVE_BALANCE"}
```

## Métricas

| Métrica | Definición | Por qué |
|---|---|---|
| **Execution match** (SQL) | el resultado de la consulta generada es igual al de la consulta esperada, como multiconjunto (el orden no importa) | mide el resultado, no la forma del SQL: hay muchas consultas correctas |
| **Retrieval hit** (RAG) | alguno de los chunks citados pertenece al documento esperado | detecta RAG que “suena bien” pero recuperó otra cosa |
| **Regla detectada** (calidad) | el hallazgo esperado aparece con `count > 0` | verifica que la regla realmente corre sobre los datos |
| **Soporte numérico** | proporción de cifras de la respuesta que existen en el resultado (más cardinalidad y ventana de vista) | aproximación barata a *faithfulness* sin juez externo |
| **Latencia p50/p95** | por caso, medidas en el proceso | el costo de un copiloto se paga en latencia percibida |
| **Costo** | `tokens × precio` por llamada, acumulado | permite comparar modelos con la misma vara |

`prompt_version` y `schema_hash` se guardan en cada reporte: si cambia el prompt o el DDL, el diff lo muestra.

## Baseline medido (rule-based, sin LLM)

Corrida del 2026-11-18, 20 casos, SQLite, `hashing` embeddings:

| Grupo | Casos | Pass | Pass rate | Latencia p95 |
|---|---|---|---|---|
| Global | 20 | 18 | **90,0%** | 7 ms |
| SQL | 15 | 13 | 86,7% | 7 ms |
| RAG | 3 | 3 | 100% | 6 ms |
| Calidad | 2 | 2 | 100% | 6 ms |

Soporte numérico promedio: **0,867** · Costo: **US$0,00**.

### Qué falla y por qué es informativo

Los dos casos que fallan son paráfrasis deliberadas:

- `sql-13` “¿Dónde se concentra el mayor volumen de unidades despachadas?” (esperado: top de salidas).
- `sql-14` “¿Qué combinaciones producto-bodega quedaron debiendo stock?” (esperado: saldo negativo).

El baseline por reglas no las reconoce porque no contienen las palabras clave. Un LLM sí debería: por eso el
baseline y el modelo real se miden con el mismo comando y el mismo conjunto. Si al conectar un modelo el pass
rate **no sube de 90%**, el modelo o el prompt están mal, no el harness.

## Cómo agregar casos

1. Añade la línea al JSONL con `id` único, `intent` y el resultado esperado.
2. Para SQL, escribe `expected_sql` legible (no la salida del modelo).
3. Ejecuta `python -m wms_copilot.cli eval` y revisa que el caso nuevo pase con el baseline **o** documenta
   por qué no (es un caso de paráfrasis).
4. Si el caso nuevo expone un hueco del prompt, actualiza `PROMPT_VERSION` y adjunta el antes/después en el
   PR.

## Limitaciones del harness

- El juez de *faithfulness* es heurístico: detecta cifras inventadas, no contradicciones semánticas.
- Un `execution match` puede ser 0 aunque la respuesta sea útil (por ejemplo, un `LIMIT` distinto). Se
  prefieren consultas sin `LIMIT` en el caso esperado salvo que el top sea el objetivo.
- El golden set es chico y fue escrito a mano: mide regresiones con fiabilidad, no calidad absoluta.
- El retriever con `hashing embeddings` es esencialmente léxico; con embeddings reales los casos de RAG
  deberían mejorar en paráfrasis.
