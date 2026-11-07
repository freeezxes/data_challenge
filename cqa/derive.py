"""Ступень считает код, а не модель.

Для трёх «размытых» критериев модель возвращает простые факты (какие вопросы задал клиент,
закрыт ли каждый, какие были сбои), а ответ выводится по правилам из текста чек-листа.
Правила взяты из чек-листа и памятки, а не из набора данных, поэтому переносятся на любые диалоги.

Распознавание критерия — по названию, чтобы при замене критерия в чек-листе разбор
применялся только к тем, к кому он относится.
"""
from __future__ import annotations

KINDS = {"needs": "выявление потребност", "consult": "консультац", "flow": "управление диалог"}


def kind_of(criterion: dict) -> str | None:
    title = criterion.get("title", "").lower()
    for k, word in KINDS.items():
        if word in title:
            return k
    return None


def _id_prop():
    return {"type": "string", "description": "ID реплики (m1, m2…)"}


def extra_schema(kind: str) -> tuple[dict, list[str]]:
    """Дополнительные поля-факты для схемы ответа."""
    if kind == "needs":
        props = {
            "request_formulated_before_operator": {"type": "boolean", "description": "Клиент назвал суть вопроса до первой реплики оператора (в том числе боту)."},
            "operator_questions": {"type": "array", "description": "Все вопросы оператора клиенту о его ситуации (не «чем ещё помочь» в конце).", "items": {
                "type": "object", "additionalProperties": False, "required": ["id", "kind"],
                "properties": {"id": _id_prop(), "kind": {"type": "string", "enum": ["needed", "known_repeat"],
                               "description": "needed — уточнение, без которого нельзя точно ответить; known_repeat — клиент это уже сообщил."}}}},
            "answered_by_guess": {"type": "boolean", "description": "Оператор ответил по догадке, не уточнив нужное, и ответ не подошёл ситуации клиента."},
            "ignored_request": {"type": "boolean", "description": "Оператор проигнорировал суть запроса клиента."},
        }
    elif kind == "consult":
        props = {
            "client_questions": {"type": "array", "description": "Все вопросы и просьбы клиента за диалог, включая заданные боту до оператора.", "items": {
                "type": "object", "additionalProperties": False, "required": ["id", "essential", "status"],
                "properties": {"id": _id_prop(),
                               "essential": {"type": "boolean", "description": "Существенный вопрос (суть обращения), а не второстепенная деталь."},
                               "status": {"type": "string", "enum": ["closed", "partial", "ignored", "wrong"],
                                          "description": "closed — ответ или корректный следующий шаг, в том числе понятный косвенный ответ, который клиент принял; partial — общая подсказка, клиенту неясно, как действовать; ignored — без ответа; wrong — ответ ошибочен."}}}},
        }
    elif kind == "flow":
        props = {
            "glitches": {"type": "array", "description": "Сбои в ясности и последовательности ответов оператора. Ошибки распознавания речи не сбой.", "items": {
                "type": "object", "additionalProperties": False, "required": ["id", "type", "client_confused"],
                "properties": {"id": _id_prop(),
                               "type": {"type": "string", "enum": ["missed_question", "repeat_known", "off_topic", "unclear_wording", "confusing_order", "unwarned_wait", "contradiction"]},
                               "client_confused": {"type": "boolean", "description": "Клиенту пришлось переспросить, потому что он не понял сам ответ (а не потому, что вопрос пропущен)."}}}},
        }
    else:
        return {}, []
    return props, list(props)


def derive(kind: str, item: dict, criterion: dict):
    """Ответ из фактов. None — фактов нет, оставляем ответ модели."""
    if kind == "needs":
        qs = item.get("operator_questions")
        if qs is None:
            return None
        if item.get("ignored_request") or item.get("answered_by_guess") or any(q["kind"] == "known_repeat" for q in qs):
            return "no"
        if any(q["kind"] == "needed" for q in qs):
            return "yes"
        return "na" if criterion.get("na_allowed") else "yes"
    if kind == "consult":
        qs = item.get("client_questions")
        if qs is None:
            return None
        if not qs:
            return "na" if criterion.get("na_allowed") else 0
        ess = [q for q in qs if q["essential"]] or qs
        if any(q["status"] == "wrong" for q in ess):
            return 0
        closed = sum(q["status"] == "closed" for q in ess)
        if closed == len(ess):
            minor_open = any(q["status"] != "closed" for q in qs if not q["essential"])
            return 15 if minor_open else 20
        if closed >= 1:
            return 10
        if any(q["status"] == "partial" for q in ess):
            return 5
        return 0
    if kind == "flow":
        gl = item.get("glitches")
        if gl is None:
            return None
        if any(g["type"] == "contradiction" for g in gl):
            return 0
        if len(gl) >= 2 or any(g["client_confused"] for g in gl):
            return 4
        return 7 if gl else 10
    return None
