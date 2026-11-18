# Diccionario de datos WMS

Definiciones operativas del modelo de inventario. Este documento es la referencia que usa el copiloto
cuando alguien pregunta por el significado de un campo.

## products

Tabla maestra de productos. `sku` es el identificador legible que usa el negocio (formato `SKU-0000`).
`is_active` en 1 indica que el producto esta vigente en el catalogo; en 0 significa que fue dado de baja
y no deberia recibir movimientos nuevos. `unit_cost` se usa para valorizar inventario y calcular el costo
promedio por categoria.

## warehouses

Bodegas fisicas. `code` sigue el formato `CD-XX` (centro de distribucion). `capacity_units` es la capacidad
declarada en unidades, no la ocupacion real: para ocupacion se debe sumar `on_hand` de `v_inventory_balance`.

## inventory_movements

Registro transaccional de inventario. `movement_type` tiene cuatro valores:

- `IN`: ingreso de mercaderia, suma al saldo.
- `OUT`: salida o despacho, resta al saldo.
- `ADJUST`: ajuste por conteo fisico, puede sumar o restar.
- `TRANSFER`: traslado entre bodegas; resta en la bodega de origen y su par suma en la de destino.

`quantity` se define siempre en positivo porque el signo lo aporta el tipo de movimiento. Una cantidad
cero o negativa es un error de captura, nunca un caso valido de negocio. `reference_doc` es el documento
de origen (guia, factura, acta de conteo) y es la clave que se usa para detectar cargas duplicadas.
`source_system` indica el sistema que origino el registro: `BOS` para la operacion comercial, `WMS` para
capturas de bodega y `SAP` para la integracion contable.

## purchase_orders

Ordenes de compra. `qty_ordered` es lo comprometido con el proveedor y `qty_received` lo efectivamente
recepcionado. El campo calculado `received_delta` es la diferencia `qty_received - qty_ordered`:

- `received_delta` positivo indica sobre-recepcion: llego mas mercaderia que la ordenada y hay que regularizar
  con compras antes de contabilizar.
- `received_delta` negativo en una orden con estado `RECEIVED` indica una inconsistencia entre WMS y SAP.
- Estado `OPEN` significa que aun no hay recepciones; `PARTIAL` que hay recepciones parciales.

## v_inventory_balance

Vista de saldo por producto y bodega. Se calcula sumando `IN` y `ADJUST`, restando `OUT` y `TRANSFER`.
Es la fuente oficial para responder preguntas de stock: no se debe recalcular desde `inventory_movements`
en cada consulta porque se corre el riesgo de aplicar mal los signos.

## etl_runs

Bitacora de ejecuciones de Airflow y del cargador de datos. `status` puede ser `RUNNING`, `SUCCESS` o
`FAILED`. `rows_processed` permite contrastar el volumen cargado contra el esperado del dia.
`error_message` se indexa en la base de conocimiento para que el copiloto pueda explicar fallas historicas.

## data_quality_issues

Hallazgos abiertos del escaneo de calidad. Cada fila referencia un `rule_code` (por ejemplo
`NEGATIVE_BALANCE` o `DUPLICATE_MOVEMENT`), una severidad (`HIGH`, `MEDIUM`, `LOW`) y una `entity_key`
que evita duplicar el mismo hallazgo entre ejecuciones consecutivas.

## Convenciones y reglas de negocio

- Todas las fechas estan en hora de Chile continental y en formato ISO-8601 (`YYYY-MM-DD HH:MM:SS`).
- Un movimiento sin `reference_doc` es aceptable solo en ajustes de conteo; en `IN` u `OUT` es sospechoso.
- El cierre diario se calcula sobre movimientos con `moved_at` del dia anterior a las 23:59.
- Los saldos negativos se toleran como estado transitorio maximo de 24 horas; despues se consideran incidente.
