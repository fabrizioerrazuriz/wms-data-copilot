# ADR 0002 — Guardrails de SQL en capas

- **Estado:** aceptado
- **Fecha:** 2026-11-18

## Contexto

Text-to-SQL sobre una base operativa es la parte peligrosa del proyecto: un `DELETE` generado por error, una
tabla fuera de alcance o una consulta que barre 2 millones de filas puede tumbar el servicio. Confiar en el
prompt (“por favor solo SELECT”) no es un control de seguridad.

## Decisión

Cinco capas independientes:

1. **Prompt** con esquema explícito y formato JSON.
2. **Parser `sqlglot`**: una sola sentencia, raíz `SELECT`/`WITH`, allowlist de tablas, bloqueo de nodos de
   escritura, exclusión de nombres de CTE del chequeo de tablas.
3. **Envoltura de ejecución** con `LIMIT n+1` y detección de truncamiento.
4. **Timeout** por motor (`statement_timeout` en Postgres, *progress handler* en SQLite).
5. **Transacción revertida siempre**: aun si algo se colara, no se confirma.

## Justificación

- La capa 2 es la que realmente bloquea: es análisis sintáctico, no coincidencia de texto, y por eso detecta
  `SELECT 1; DROP TABLE` o `WITH x AS (DELETE ...)`.
- La capa 3 evita el agotamiento de memoria con independencia de lo que el modelo escriba.
- La capa 5 hace que un error de clasificación no tenga consecuencias persistentes: la conexión opera siempre
  en modo lectura efectivo.
- Un único reintento con el error del guardrail mejora la tasa de éxito sin habilitar bucles costosos.

## Consecuencias

- Consultas legítimamente enormes se truncan por diseño: es el comportamiento correcto en un copiloto.
- El costo de parseo es despreciable (milisegundos) frente a la llamada al modelo.
- Se debe mantener la allowlist `ALLOWED_TABLES` sincronizada con el DDL; hay un test que detecta *drift*.
- Los rechazos quedan en el log (`sql_attempt_failed`) con el motivo, lo que además sirve para mejorar el
  prompt con datos reales.
