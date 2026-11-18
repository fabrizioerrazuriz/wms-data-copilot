# Cómo modificar la información y ver el efecto en la respuesta

Hay tres capas de información que puedes cambiar, y cada una afecta a un tipo distinto de respuesta.
Todos los ejemplos de esta página están ejecutados y medidos, no son ilustrativos.

| Capa | Archivo | Cómo se aplica | Qué cambia |
|---|---|---|---|
| **Datos** (inventario, OC, ETL) | `data/raw/*.csv` | `python -m wms_copilot.cli reset --yes` | las cifras y filas de las respuestas SQL y los hallazgos de calidad |
| **Movimiento manual** (+/- unidades) | `data/raw/inventory_movements.csv` | `python -m wms_copilot.cli adjust SKU-0003 1000 --warehouse CD-03` | el saldo del producto en esa bodega |
| **Documentación** (RAG) | `kb/*.md` | `python -m wms_copilot.cli reindex` | el texto de las respuestas de documentación y las fuentes citadas |
| **Prompt y modelo** | `src/wms_copilot/llm/prompts.py`, `.env` | reiniciar la API | el SQL generado, el estilo y el pass rate de `eval` |

La regla de oro:

- `bootstrap` **regenera** los CSV con el seed `20261101` y sobrescribe tus ediciones.
- `reset --yes` **borra las tablas y recarga los CSV que ya tienes**, sin regenerarlos.
- `load` solo carga lo que falte (no borra nada); úsalo para añadir filas nuevas.
- `adjust` agrega un movimiento, calcula el `movement_id` siguiente y lo carga: es la forma segura de sumar
  o restar unidades.

## Agregar 1.000 unidades de un producto (sin editar el CSV)

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m wms_copilot.cli adjust SKU-0003 1000 --warehouse CD-03 --type IN --reason "Ingreso manual 1000 unidades"
```

Salida verificada:

```json
{
  "movement_id": 2412,
  "sku": "SKU-0003",
  "warehouse": "CD-03",
  "movement_type": "IN",
  "quantity": 1000,
  "inserted": 1,
  "on_hand_before": 119,
  "on_hand_after": 1119,
  "reference_doc": "Ingreso manual 1000 unidades"
}
```

Y el saldo total del producto pasa de `-990` a `10` (`-990 + 1000`).

Detalles del comando:

- `--warehouse` es obligatorio: el saldo es por producto **y** bodega.
- `--type` por defecto es `ADJUST` (ajuste de inventario). Usa `IN` para un ingreso real y `OUT` para una
  salida. Solo `ADJUST` acepta cantidad negativa.
- `--reason` queda como `reference_doc`, así el movimiento es rastreable.
- El movimiento se escribe en el CSV **y** se carga en la base. Es idempotente por `movement_id`: repetir
  el comando agrega otro movimiento (es un alta, no una actualización).

### No edites el CSV con Excel

Si abres `data/raw/inventory_movements.csv` en Excel y guardas, Excel en español cambia el separador a
`;`, reescribe las fechas como `DD-MM-AAAA` y puede dejar la fila con un `movement_id` duplicado. El
pipeline ahora tolera las dos primeras cosas (detecta el separador y normaliza la fecha), pero el
`movement_id` duplicado se rechaza como error estructural. Por eso, para agregar o quitar unidades usa
`adjust`, no Excel.

Además, `reset` valida el CSV **antes** de borrar: si no puede leer ninguna fila válida, aborta sin tocar
los datos que ya están cargados.

---

## Experimento 1 — Cambiar un dato y ver el número

Pregunta de control (saldo total del producto `SKU-0003`):

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m wms_copilot.cli ask "¿Cuál es el saldo total del producto SKU-0003?"
```

Antes: `-990`.

Añade una entrada de inventario de 5.000 unidades en el CSV (formato:
`movement_id, movement_type, product_id, warehouse_id, quantity, moved_at, reference_doc, source_system`):

```powershell
& $py -c "p='data/raw/inventory_movements.csv'; open(p,'a',encoding='utf-8').write('25000,IN,3,1,5000,2026-11-13 09:00:00,DOC-DEMO-1,BOS\n')"
& $py -m wms_copilot.cli reset --yes
& $py -m wms_copilot.cli ask "¿Cuál es el saldo total del producto SKU-0003?"
```

Después: `4010` (porque `-990 + 5000 = 4010`).

El SQL no cambió; cambió lo que devuelve. Eso es exactamente lo que se busca: **las cifras salen de la
base, nunca del modelo**. Si repites `reset --yes`, el resultado sigue siendo `4010` (la recarga es
idempotente por clave natural); si corres `bootstrap`, vuelve a `-990` porque regenera los CSV.

Para sumar unidades sin tocar el CSV, el equivalente seguro es
`adjust SKU-0003 1000 --warehouse CD-03` (ver arriba).

Para revertir sin regenerar todo: elimina la línea con `DOC-DEMO-1` del CSV y vuelve a ejecutar `reset --yes`.

## Experimento 2 — Cambiar la documentación y ver la respuesta RAG

Pregunta de control:

```powershell
& $py -m wms_copilot.cli ask "¿Cada cuánto se hace el conteo cíclico y qué cobertura tiene?"
```

Antes (la KB no habla del tema), responde con lo que más se parece, que es incorrecto:

```
`movement_type` tiene cuatro valores: ...
Fuentes: 01_diccionario_datos#3, ...
```

Añade una sección al final de `kb/01_diccionario_datos.md`:

```markdown
## Conteo ciclico

El conteo ciclico se ejecuta todos los martes a las 07:00. Su cobertura es el 15% de las posiciones
activas de cada bodega. Lo realiza el equipo de bodega con un colector RF y el resultado se compara
contra `v_inventory_balance` antes de publicar el saldo. Si la diferencia supera el 0,5% de las
posiciones contadas, se abre un incidente de inventario y se bloquea la publicacion del saldo.
```

Reindexa y vuelve a preguntar:

```powershell
& $py -m wms_copilot.cli reindex
& $py -m wms_copilot.cli ask "¿Cada cuánto se hace el conteo cíclico y qué cobertura tiene?"
```

Después:

```
El conteo ciclico se ejecuta todos los martes a las 07:00. Su cobertura es el 15% de las posiciones
activas de cada bodega. [01_diccionario_datos#9]

Fuentes: 01_diccionario_datos#9, ...
```

El número de chunks pasa de 32 a 33 y la cita apunta al fragmento nuevo. Para revertir:
`git checkout -- kb/` y `reindex`.

### Qué hace que una sección nueva se recupere bien

- Usa en el texto **las mismas palabras de la pregunta** ("cobertura", "conteo", "cíclico"). El
  embedding offline es léxico (hashing de palabras y n-gramas), no semántico: no adivina sinónimos.
- Las tildes ya no importan: el tokenizador normaliza (`cíclico` = `ciclico`).
- Si un documento es muy largo, se trocea por secciones de 900 caracteres con solape de 120; una
  sección por tema produce mejores fragmentos que un párrafo gigante.
- El modo `rule-based` responde **extractivamente**: elige la frase del contexto con más palabras
  ponderadas por rareza y la cita. Un LLM real redacta mejor, pero se apoya en los mismos fragmentos.

## Experimento 3 — Cambiar el prompt o el modelo

1. **Modelo**: edita `.env` (`WMS_LLM_PROVIDER=openai|anthropic|ollama`, `WMS_LLM_MODEL`, la key) y
   reinicia la API.
2. **Prompt**: `src/wms_copilot/llm/prompts.py`. Cualquier cambio de prompt debe subir
   `PROMPT_VERSION`; el `eval_report.json` lo guarda y así puedes comparar dos versiones.
3. **Medición**: `& $py -m wms_copilot.cli eval` antes y después. Si el pass rate no sube, el cambio
   no sirvió.

## Ver el efecto en la interfaz web

`.\iniciar.cmd` levanta la API en <http://127.0.0.1:8000>. Los badges muestran el proveedor y modelo
activos, así que al cambiar `.env` y reiniciar se ve el cambio sin tocar código. Cada respuesta
incluye el SQL ejecutado, las fuentes (RAG) o los hallazgos (calidad), más latencia y `request_id`.

## Qué NO cambia al editar

- **Guardrails**: el SQL se valida con `sqlglot` antes de ejecutarse, venga del modelo que venga.
- **Reglas de calidad**: son SQL fijo; los datos nuevos cambian los hallazgos, no las reglas.
- **Evaluación**: el golden set es fijo (`src/wms_copilot/evals/golden_set.jsonl`); si cambias los
  datos, los resultados esperados dejan de coincidir y el `eval` fallará. Es intencional: el golden
  set está calibrado contra el seed, así que para experimentar con datos usa preguntas sueltas
  (`ask`) o corre `bootstrap` antes de `eval`.
