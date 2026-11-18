# Incidentes historicos WMS y trazabilidad BOS-WMS-SAP

Registro de incidentes reales (anonimizados) y como se resolvieron. Sirve para que el copiloto pueda
explicar sintomas parecidos sin depender de la memoria del equipo.

## INC-2026-014: doble carga de documentos desde SAP

Sintoma: el saldo de tres productos aumento el doble de lo esperado en la bodega CD-02. La conciliacion
contra SAP mostro el mismo documento contabilizado dos veces con distinto `movement_id`.

Causa: el reintento automatico de la interfaz SAP reenvio el lote completo cuando el ACK excedio el timeout.
El cargador no filtraba por referencia documental, solo por `movement_id`.

Solucion: se agrego validacion de duplicado por `reference_doc + product_id + warehouse_id + quantity` y la
carga paso a ser idempotente. Se reproceso el lote sin generar filas nuevas.

## INC-2026-021: saldo negativo por traslado incompleto

Sintoma: `v_inventory_balance` mostro -390 unidades para `SKU-0007` en CD-02. El fisico si tenia stock.

Causa: el movimiento `TRANSFER` de salida se registro en origen, pero el ingreso en destino fallo por un
campo nulo en la guia. El saldo quedo descuadrado durante 6 horas.

Solucion: se agrego la regla `NEGATIVE_BALANCE` con alerta a las 24 horas y se corrigio el mapeo de la guia
de traslado. El reproceso se hizo con la misma referencia para no duplicar.

## INC-2026-028: sobre-recepcion no detectada

Sintoma: cinco ordenes de compra quedaron con `qty_received` mayor a `qty_ordered` y estado `RECEIVED`.

Causa: el proveedor entrego mas unidades de las comprometidas y el WMS acepto la recepcion sin bloqueo.

Solucion: regla `OVER_RECEPTION` en el escaneo de calidad y regularizacion contable en SAP. Se acordo con
compras que toda diferencia mayor a 3% requiere aprobacion previa.

## Trazabilidad entre sistemas

El flujo de un movimiento es BOS (documento comercial) -> WMS (operacion de bodega) -> SAP (contabilidad).
Cada salto debe conservar la referencia documental:

- BOS genera la guia o factura y la envia al WMS.
- El WMS registra el movimiento fisico y devuelve el ACK con `movement_id`.
- SAP contabiliza usando la misma `reference_doc`.

Si un salto pierde la referencia, el dato queda huerfano y no se puede conciliar. La validacion de integridad
compara conteos por referencia entre los tres sistemas todos los dias a las 03:00.

## Senales de alerta tempranas

- Aumento de movimientos con `source_system = 'SAP'` sin `reference_doc`.
- Ordenes `RECEIVED` con `received_delta` distinto de cero.
- Movimientos de productos con `is_active = 0`, sintoma de catalogo desactualizado.
- Caidas repetidas del DAG en la misma tarea: indica problema de esquema en origen, no de infraestructura.

## Escalamiento

Severidad HIGH: se avisa a operaciones y a integracion de inmediato, con el `run_id` y la consulta que
evidencia el problema. Severidad MEDIA: se resuelve en el mismo dia antes del cierre. Severidad LOW: se
incluye en el informe semanal de calidad.
