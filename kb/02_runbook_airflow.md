# Runbook de carga diaria (Airflow + WMS)

Procedimiento operativo para la carga diaria de movimientos WMS hacia SQL Server y la actualizacion
del saldo publicado.

## DAG wms_daily_load

Secuencia de tareas:

1. `extract_wms`: extrae los movimientos del dia desde el WMS y los deja en CSV en la zona de staging.
2. `validate_types`: valida tipos, claves foraneas y cantidades. Si el error estructural supera el 1% del lote,
   el DAG falla y no carga nada.
3. `load_sqlserver`: carga idempotente. Cada fila se inserta con su clave natural y los duplicados se ignoran.
4. `reconcile_balance`: compara el conteo de movimientos y el saldo esperado por producto y bodega.
5. `notify`: publica el resumen en el canal de operaciones.

El DAG se ejecuta a las 02:00 hora de Chile y tiene un SLA de 45 minutos.

## Reproceso de un lote sin duplicar movimientos

El reproceso es seguro porque la carga es idempotente: la clave natural (`movement_id`) y la referencia
documental evitan insertar dos veces la misma fila.

Pasos:

1. Identificar el `run_id` fallido en `etl_runs` y el rango de fechas afectado.
2. Corregir la causa raiz (archivo de origen, tipo de dato o conexion).
3. Reejecutar solo la tarea afectada: `airflow tasks run wms_daily_load load_sqlserver <fecha>`.
4. Ejecutar `reconcile_balance` y comparar `rows_processed` contra el conteo de filas del CSV.
5. Si el conteo no coincide, revisar `data/rejected_*.csv` y volver a correr la validacion.

Nunca se debe truncar la tabla para "empezar de nuevo": eso rompe la trazabilidad y duplica documentos ya
contabilizados en SAP.

## Que hacer si la carga falla por un duplicado

Si el error es `UNIQUE constraint failed` o `violacion de UNIQUE en inventory_movements.reference_doc`:

1. Verificar si el documento ya existe con `SELECT * FROM inventory_movements WHERE reference_doc = '<doc>'`.
2. Si existe una sola fila con el mismo producto, bodega y cantidad, es un reproceso del mismo documento:
   descartar la fila del lote y continuar.
3. Si existen dos o mas filas con la misma referencia y distinto `movement_id`, hay doble carga desde SAP:
   conservar la de menor `movement_id`, marcar el resto como anomalia y avisar a integracion.
4. Registrar el incidente en `data_quality_issues` mediante el escaneo de calidad, no a mano.

## Ventanas y dependencias

- La carga de compras (`purchase_orders`) debe terminar antes de la conciliacion: si llega tarde, el saldo
  y el estado de recepcion quedan desalineados.
- La integracion con SAP corre cada 30 minutos; un reproceso manual debe ejecutarse fuera de esa ventana.
- Si el WMS esta en mantencion, el DAG se salta la fecha con `catchup=False` y se deja constancia en `etl_runs`.

## Verificacion posterior

- `rows_processed` mayor a cero y menor al 120% del promedio de los ultimos 7 dias.
- Cero saldos negativos nuevos en `v_inventory_balance`.
- Cero diferencias entre `qty_received` y `qty_ordered` en ordenes con estado `RECEIVED`.
