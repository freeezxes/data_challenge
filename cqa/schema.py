"""JSON-схема ответа модели, собранная из чек-листа (structured outputs).

Порядок полей задан намеренно: сначала реплики-доказательства и проверенный участок,
потом рассуждение, и только потом ответ — модель сначала опирается на текст.
"""
from __future__ import annotations

from .checklist import allowed_answers


def criterion_schema(criterion: dict) -> dict:
    props = {
        "evidence_ids": {
            "type": "array", "items": {"type": "string"},
            "description": "ID реплик (m1, m2…), на которых основан вывод, включая системные сообщения.",
        },
        "checked_range": {
            "type": "string",
            "description": "Проверенный участок, например «m10–m14». Для пропущенного действия — где оно ожидалось.",
        },
        "reasoning": {
            "type": "string",
            "description": "Коротко по-русски: что подтверждают реплики; для N/A — какое условие неприменимости выполнено.",
        },
        "answer": {"type": "string", "enum": allowed_answers(criterion)},
    }
    required = ["evidence_ids", "checked_range", "reasoning", "answer"]
    if criterion["type"] == "critical":
        props["violations"] = {
            "type": "array",
            "items": {"type": "string", "enum": [v["id"] for v in criterion.get("violations", [])]},
            "description": "Какие грубые нарушения найдены (пусто, если ответ «no»).",
        }
        required.append("violations")
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def output_schema(checklist: dict) -> dict:
    criteria = checklist["criteria"]
    return {
        "type": "object",
        "properties": {
            "criteria": {
                "type": "object",
                "properties": {c["id"]: criterion_schema(c) for c in criteria},
                "required": [c["id"] for c in criteria],
                "additionalProperties": False,
            },
            "summary": {"type": "string", "description": "Одно предложение по-русски: главное о работе оператора."},
        },
        "required": ["criteria", "summary"],
        "additionalProperties": False,
    }
