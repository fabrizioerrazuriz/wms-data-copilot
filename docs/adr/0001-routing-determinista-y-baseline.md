# ADR 0001 — Routing determinista y baseline sin LLM

- **Estado:** aceptado
- **Fecha:** 2026-11-18

## Contexto

El copiloto debe elegir entre tres herramientas (SQL, documentación, calidad) y además poder demostrarse y
probarse sin credenciales de un proveedor de LLM.

## Decisión

1. La clasificación de intención es **determinista** (keywords normalizados sin acentos), no una llamada al
   modelo.
2. Se incluye un proveedor `rule-based` que implementa el mismo contrato `LLMProvider` y resuelve las
   intenciones más frecuentes con reglas explícitas.

## Justificación

- La clasificación por keywords cuesta microsegundos y es 100% reproducible: un test puede afirmar
  `classify("...") == "docs"` sin mocks.
- Si el modelo decide el flujo, cada evaluación arrastra dos fuentes de error (routing + tool) y el
  diagnóstico se vuelve ambiguo.
- El baseline permite correr CI y la demo sin API key, y da una vara objetiva: “el LLM debe superar 90%”.

## Consecuencias

- Intenciones nuevas requieren tocar `QUALITY_HINTS`/`DOCS_HINTS` (una línea y un test).
- El baseline falla en paráfrasis (ver `docs/evals.md`), lo cual es intencional: muestra el límite del enfoque.
- Migrar a routing con LLM es un cambio aislado en `classify()`; el resto del sistema no se entera.
