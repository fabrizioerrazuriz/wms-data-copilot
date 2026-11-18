# Reglas de calidad de datos del WMS

Catalogo de reglas que ejecuta el agente de calidad. Cada regla es una consulta SQL determinista: el LLM
solo redacta el resumen, nunca calcula los hallazgos.

## NEGATIVE_BALANCE (HIGH)

Detecta combinaciones producto-bodega con `on_hand < 0` en `v_inventory_balance`. Un saldo negativo indica
que las salidas superan a los ingresos: error de captura, traslado incompleto o doble carga de salidas.
Accion: identificar el ultimo documento involucrado y reprocesar con la misma referencia.

## INVALID_QUANTITY (HIGH)

Detecta movimientos con `quantity <= 0`. La cantidad debe ser siempre positiva porque el signo lo aporta
`movement_type`. Accion: anular el movimiento en el WMS y volver a capturarlo.

## DUPLICATE_MOVEMENT (MEDIUM)

Detecta grupos con la misma `reference_doc`, `product_id`, `warehouse_id` y `quantity` repetidos mas de una
vez. Es la senal clasica de doble carga desde SAP. Accion: conservar el primer `movement_id`, marcar el resto
y reejecutar la conciliacion.

## OVER_RECEPTION (MEDIUM)

Detecta ordenes con `qty_received > qty_ordered`. Requiere regularizacion con compras y ajuste contable.
Accion: emitir nota de credito o ajustar la orden segun el acuerdo con el proveedor.

## STATUS_MISMATCH (MEDIUM)

Detecta ordenes con estado `RECEIVED` y `qty_received <> qty_ordered`. Rompe el cierre de recepciones y la
conciliacion con SAP. Accion: corregir el estado en el WMS y reenviar el documento.

## INACTIVE_PRODUCT_MOVEMENT (LOW)

Detecta movimientos de productos con `is_active = 0`. Suele indicar catalogo desactualizado o productos
reactivados sin actualizar el maestro. Accion: revisar con el area de datos maestros.

## ETL_FAILURES (LOW)

Detecta ejecuciones con `status = 'FAILED'` en `etl_runs`. Una falla aislada es normal; tres consecutivas en
el mismo DAG indican un problema estructural en el origen. Accion: revisar `error_message` y el runbook.

## Umbrales y gobierno

- El escaneo se ejecuta al final de cada carga y antes de publicar el saldo del dia.
- Severidad HIGH bloquea la publicacion del reporte hasta que exista una justificacion registrada.
- Los hallazgos se guardan en `data_quality_issues` sin duplicar el mismo `rule_code` entre corridas.
- Todo cambio de umbral se versiona en este documento y en el golden set de evaluacion del copiloto.

## Reproceso seguro

Un reproceso solo es aceptable si es idempotente: la clave natural evita duplicados y la transaccion se
revierte completa si la validacion falla. Nunca se borra el historico para "limpiar" un lote.
