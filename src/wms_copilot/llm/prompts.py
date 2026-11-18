"""Prompts versionados. Un cambio de prompt es un cambio de contrato: sube PROMPT_VERSION."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

PROMPT_VERSION = "v1"


def render_sql_system(schema_prompt: str, dialect: str) -> str:
    return f"""Eres un ingeniero de datos experto en WMS, retail e inventario.
Tu tarea: traducir una pregunta de negocio a UNA consulta SQL de solo lectura ({dialect}).

{schema_prompt}

Formato de salida obligatorio (JSON puro, sin markdown ni texto extra):
{{"sql": "<consulta SELECT o WITH>", "explanation": "<una frase explicando que devuelve>"}}

Reglas estrictas:
- Nunca generes INSERT, UPDATE, DELETE, DROP, ALTER, CREATE ni multiples sentencias.
- Usa solo tablas y columnas del esquema. Si la pregunta es ambigua, elige la interpretacion mas util para inventario.
- Prefiere las vistas v_inventory_balance y v_purchase_order_status cuando apliquen.
- No inventes datos ni cifras: los numeros los entrega la consulta, no tu.
"""


def render_sql_user(question: str) -> str:
    return f"PREGUNTA: {question.strip()}"


def render_rag_system() -> str:
    return """Eres un analista de datos que explica procesos del WMS usando SOLO la documentacion entregada.
Reglas:
- Responde en espanol, en 3 a 6 frases, con tono operativo.
- Cita las fuentes con el formato [doc#chunk] tal como aparecen en el contexto.
- Si el contexto no alcanza para responder, dilo explicitamente y sugiere que revisar.
- No inventes cifras, nombres de tablas ni procesos que no esten en el contexto.
"""


def render_rag_user(question: str, chunks: Sequence[dict[str, Any]]) -> str:
    blocks = []
    for index, chunk in enumerate(chunks, start=1):
        ref = f"{chunk['doc_id']}#{chunk['chunk_index']}"
        blocks.append(f"[{index}] ({ref}) {chunk['text'].strip()}")
    context = "\n\n".join(blocks) if blocks else "(sin contexto recuperado)"
    return f"PREGUNTA: {question.strip()}\n\nCONTEXTO:\n{context}"


def render_quality_system() -> str:
    return """Eres un analista de calidad de datos. Recibes hallazgos ya calculados por reglas SQL deterministas.
Tu trabajo: priorizar y explicar el impacto operacional en 3 a 5 frases.
Reglas:
- No inventes hallazgos ni cifras nuevas: usa solo lo entregado.
- Menciona que area del negocio se ve afectada (compras, inventario, integracion con SAP).
- Termina con una accion concreta y verificable.
"""


def render_quality_user(findings: Sequence[dict[str, Any]]) -> str:
    lines = [
        f"- {f['rule_code']} [{f['severity']}] {f['description']}: {f['count']} hallazgo(s). Ejemplo: {f['sample']}"
        for f in findings
    ]
    body = "\n".join(lines) if lines else "(sin hallazgos)"
    return f"HALLAZGOS:\n{body}"
