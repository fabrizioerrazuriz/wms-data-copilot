"""Proveedor determinista: baseline sin LLM.

Su proposito es doble:
1. Permitir que el proyecto, los tests y la CI corran sin credenciales ni costo.
2. Servir de baseline medible en las evaluaciones ("rule-based vs LLM").
No pretende ser robusto: si no reconoce la intencion, responde que no puede,
y eso cuenta como fallo en el harness de evaluacion. Esa es la gracia.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata

from ..telemetry import Timer
from .base import LLMResponse, estimate_cost, estimate_tokens


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _extract_question(user: str) -> str:
    match = re.search(r"PREGUNTA:\s*(.+)", user)
    return match.group(1).strip() if match else user.strip()


def _has(q: str, *needles: str) -> bool:
    return all(needle in q for needle in needles)


def _any(q: str, *needles: str) -> bool:
    return any(needle in q for needle in needles)


def _sku_filter(q: str) -> str | None:
    match = re.search(r"sku[-\s_]?(\d{1,5})", q)
    if not match:
        return None
    return f"SKU-{int(match.group(1)):04d}"


def rule_based_sql(question: str) -> tuple[str, str] | None:
    """Mapea intenciones frecuentes a SQL. Devuelve (sql, explanation) o None."""
    q = _normalize(question)
    sku = _sku_filter(q)

    if sku and _any(q, "saldo", "stock", "unidades"):
        return (
            f"SELECT SUM(on_hand) AS saldo_total FROM v_inventory_balance WHERE sku = '{sku}'",
            f"Saldo total en unidades del producto {sku}.",
        )

    if _any(q, "saldo negativo", "saldos negativos", "negativo") and _any(q, "saldo", "bodega", "producto"):
        return (
            "SELECT sku, warehouse_code, on_hand FROM v_inventory_balance"
            " WHERE on_hand < 0 ORDER BY on_hand ASC",
            "Combinaciones producto-bodega con saldo negativo.",
        )

    if _any(q, "sobre-recepcion", "sobre recepcion", "recibido mas", "recibieron mas", "exceso de recepcion"):
        return (
            "SELECT po_number, sku, qty_ordered, qty_received, received_delta"
            " FROM v_purchase_order_status WHERE qty_received > qty_ordered ORDER BY received_delta DESC",
            "Ordenes de compra recepcionadas por sobre lo ordenado.",
        )

    if _any(q, "ordenes de compra", "orden de compra", "oc ") and _any(q, "pendiente", "abierta", "sin recibir"):
        return (
            "SELECT status, COUNT(*) AS ordenes FROM purchase_orders"
            " WHERE status IN ('OPEN', 'PARTIAL') GROUP BY status ORDER BY ordenes DESC",
            "Ordenes de compra pendientes de recepcion agrupadas por estado.",
        )

    if _any(q, "movimientos por tipo", "tipo de movimiento", "tipos de movimiento", "movimientos hay por tipo"):
        return (
            "SELECT movement_type, COUNT(*) AS movimientos, SUM(quantity) AS unidades"
            " FROM inventory_movements GROUP BY movement_type ORDER BY movimientos DESC",
            "Cantidad de movimientos y unidades por tipo.",
        )

    if _any(q, "salidas", "salida") and _any(q, "mayor", "mas", "top", "mayores"):
        return (
            "SELECT p.sku, SUM(m.quantity) AS unidades_salida FROM inventory_movements m"
            " JOIN products p ON p.product_id = m.product_id"
            " WHERE m.movement_type = 'OUT' GROUP BY p.sku ORDER BY unidades_salida DESC LIMIT 10",
            "Productos con mayor volumen de salidas.",
        )

    if _any(q, "sap") and _any(q, "movimiento", "provienen", "origen"):
        return (
            "SELECT COUNT(*) AS movimientos FROM inventory_movements WHERE source_system = 'SAP'",
            "Movimientos cuyo sistema de origen es SAP.",
        )

    if _any(q, "capacidad"):
        return (
            "SELECT code, name, city, capacity_units FROM warehouses ORDER BY capacity_units DESC",
            "Capacidad declarada por bodega.",
        )

    if _any(q, "cantidad cero", "cantidad negativa", "cantidades negativas", "quantity cero"):
        return (
            "SELECT movement_id, movement_type, quantity, source_system FROM inventory_movements"
            " WHERE quantity <= 0 ORDER BY movement_id",
            "Movimientos con cantidad cero o negativa (anomalia de captura).",
        )

    if _any(q, "activo", "activos") and _any(q, "categoria"):
        return (
            "SELECT category, COUNT(*) AS productos FROM products WHERE is_active = 1"
            " GROUP BY category ORDER BY productos DESC",
            "Productos activos por categoria.",
        )

    if _any(q, "etl") and _any(q, "fall", "error", "fallaron"):
        return (
            "SELECT dag_id, COUNT(*) AS ejecuciones FROM etl_runs WHERE status = 'FAILED'"
            " GROUP BY dag_id ORDER BY ejecuciones DESC",
            "Ejecuciones de ETL fallidas por DAG.",
        )

    if _any(q, "costo") and _any(q, "promedio", "medio", "promedio unitario"):
        return (
            "SELECT category, ROUND(AVG(unit_cost), 2) AS costo_promedio FROM products"
            " GROUP BY category ORDER BY costo_promedio DESC",
            "Costo unitario promedio por categoria.",
        )

    if _any(q, "duplicad"):
        return (
            "SELECT reference_doc, product_id, warehouse_id, quantity, COUNT(*) AS repeticiones"
            " FROM inventory_movements WHERE reference_doc IS NOT NULL"
            " GROUP BY reference_doc, product_id, warehouse_id, quantity HAVING COUNT(*) > 1",
            "Movimientos con la misma referencia, producto, bodega y cantidad (posible doble carga).",
        )

    if _any(q, "saldo por bodega", "inventario por bodega", "unidades por bodega"):
        return (
            "SELECT warehouse_code, SUM(on_hand) AS unidades FROM v_inventory_balance"
            " GROUP BY warehouse_code ORDER BY unidades DESC",
            "Unidades en stock por bodega.",
        )

    if _any(q, "cuantos productos", "total de productos", "cantidad de productos"):
        return ("SELECT COUNT(*) AS productos FROM products", "Total de productos maestros.")

    return None


class RuleBasedProvider:
    """Implementa LLMProvider sin red. Se usa por defecto para que nada dependa de una API."""

    name = "rule-based"

    def __init__(self, model: str = "rules-v1") -> None:
        self.model = model

    def complete(
        self,
        *,
        system: str,
        user: str,
        task: str = "generic",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        timer = Timer()
        if task == "text_to_sql":
            question = _extract_question(user)
            match = rule_based_sql(question)
            if match is None:
                payload = {
                    "sql": "",
                    "explanation": "Modo offline: no pude mapear esta pregunta a una regla conocida.",
                }
            else:
                payload = {"sql": match[0], "explanation": match[1]}
            text = json.dumps(payload, ensure_ascii=False)
        elif task == "rag_answer":
            text = self._extractive_answer(user)
        elif task == "quality_summary":
            text = (
                "Resumen offline: los hallazgos provienen de reglas SQL deterministas. "
                "Revisa primero los de severidad HIGH porque afectan la conciliacion de inventario "
                "y la integracion con SAP. Accion sugerida: reprocesar la carga del lote afectado y "
                "volver a ejecutar el escaneo de calidad."
            )
        else:
            text = f"Modo offline: tarea '{task}' sin modelo configurado."

        tokens_in = estimate_tokens(system + user)
        tokens_out = estimate_tokens(text)
        return LLMResponse(
            text=text,
            model=self.model,
            provider=self.name,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=estimate_cost(self.model, tokens_in, tokens_out),
            latency_ms=timer.elapsed_ms,
        )

    @staticmethod
    def _extractive_answer(user: str) -> str:
        """Baseline extractivo: elige la oracion del contexto con mayor solape con la pregunta."""
        question = _extract_question(user)
        blocks = re.findall(r"\[\d+\]\s*\(([^)]+)\)\s*(.+?)(?=\n\n\[\d+\]|\Z)", user, re.DOTALL)
        if not blocks:
            return "No encontre informacion suficiente en la base de conocimiento para responder."

        query_words = {
            word for word in re.findall(r"[a-z0-9ñ]+", _normalize(question)) if len(word) > 3 and word not in STOPWORDS
        }
        # Peso por rareza dentro del contexto: una palabra que aparece en un solo bloque
        # (p. ej. "duplicado") vale mas que una omnipresente ("carga").
        normalized_blocks = [(ref, text, _normalize(text)) for ref, text in blocks]
        weights = {
            word: math.log(1 + len(blocks) / (1 + sum(1 for _, _, body in normalized_blocks if word in body)))
            for word in query_words
        }

        best: tuple[float, str, str] | None = None
        for ref, text, _ in normalized_blocks:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
            for index, sentence in enumerate(sentences):
                normalized = _normalize(sentence)
                score = sum(weight for word, weight in weights.items() if word in normalized)
                if score and (best is None or score > best[0]):
                    follow = sentences[index + 1] if index + 1 < len(sentences) else ""
                    best = (score, ref, f"{sentence} {follow}".strip())

        if best is None:
            ref, text = blocks[0]
            return f"{text.strip()[:280]} [{ref}]"
        _, ref, excerpt = best
        return f"{excerpt} [{ref}]"


STOPWORDS = {
    "para",
    "como",
    "cuando",
    "donde",
    "porque",
    "sobre",
    "entre",
    "desde",
    "hasta",
    "segun",
    "este",
    "esta",
    "estos",
    "estas",
    "debe",
    "deben",
    "puede",
    "hola",
    "cada",
    "cuanto",
    "cuanta",
    "hace",
    "hacer",
    "sirve",
    "significa",
    "documentacion",
}
