"""Ступень считает код, а не модель.

Для трёх «размытых» критериев модель возвращает простые факты (какие вопросы задал клиент,
закрыт ли каждый, какие были сбои), а ответ выводится по правилам из текста чек-листа.
Правила взяты из чек-листа и памятки, а не из набора данных, поэтому переносятся на любые диалоги.

Распознавание критерия — по названию, чтобы при замене критерия в чек-листе разбор
применялся только к тем, к кому он относится.
"""
from __future__ import annotations

import re

KINDS = {"needs": "выявление потребност", "consult": "консультац", "flow": "управление диалог"}


def kind_of(criterion: dict) -> str | None:
    title = criterion.get("title", "").lower()
    for k, word in KINDS.items():
        if word in title:
            return k
    return None


def extra_schema(kind: str) -> tuple[dict, list[str]]:
    """Дополнительные поля-факты для схемы ответа.

    Списки меток — строкой через запятую: массивы enum во всех трёх критериях вместе
    превышают предел грамматики structured outputs. Метки разбирает и проверяет код.
    """
    arr = lambda enum, desc: {"type": "string", "description": f"{desc} Метки через запятую из списка: {', '.join(enum)}; пустая строка — нет ни одного."}  # noqa: E731
    if kind == "needs":
        props = {
            "operator_questions": arr(["needed", "known_repeat"], "По одной метке на каждый вопрос оператора о ситуации клиента: needed — нужное уточнение; known_repeat — клиент это уже сообщил (в том числе боту)."),
            "answered_by_guess": {"type": "boolean", "description": "Ответил по догадке без нужного уточнения, и ответ не подошёл ситуации клиента."},
            "ignored_request": {"type": "boolean", "description": "Оператор проигнорировал суть запроса."},
        }
    elif kind == "consult":
        props = {"client_questions": arr(["essential_closed", "essential_partial", "essential_ignored", "essential_wrong", "minor_closed", "minor_open"],
            "По одной метке на каждый вопрос или просьбу клиента за диалог (включая заданные боту). essential — суть обращения, minor — второстепенная деталь. closed — ответ или корректный следующий шаг, в том числе понятный косвенный ответ, который клиент принял; partial — общая подсказка, клиенту неясно, как действовать; ignored — без ответа; wrong — ошибочный ответ.")}
    elif kind == "flow":
        props = {
            "glitches": arr(["missed_question", "repeat_known", "off_topic", "unclear_wording", "confusing_order", "unwarned_wait", "contradiction"],
                "По одной метке на каждый сбой в ясности и последовательности оператора. Ошибки распознавания речи — не сбой."),
            "client_confused": {"type": "boolean", "description": "Клиенту пришлось переспросить, потому что он не понял сам ответ (а не потому, что вопрос пропущен)."},
        }
    else:
        return {}, []
    return props, list(props)


LABELS = {"operator_questions": ["needed", "known_repeat"],
          "client_questions": ["essential_closed", "essential_partial", "essential_ignored", "essential_wrong", "minor_closed", "minor_open"],
          "glitches": ["missed_question", "repeat_known", "off_topic", "unclear_wording", "confusing_order", "unwarned_wait", "contradiction"]}


def labels(item: dict, key: str):
    """Список меток из строки «a, b» (или из списка). None — поля нет."""
    raw = item.get(key)
    if raw is None:
        return None
    parts = raw if isinstance(raw, list) else re.split(r"[,;\s]+", raw)
    return [p.strip().lower() for p in parts if p and p.strip()]


def unknown_labels(item: dict) -> list[str]:
    """Метки вне допустимого списка — повод для повторного запроса."""
    bad = []
    for key, allowed in LABELS.items():
        for lab in labels(item, key) or []:
            if lab not in allowed:
                bad.append(f"{key}: {lab}")
    return bad


def derive(kind: str, item: dict, criterion: dict):
    """Ответ из фактов. None — фактов нет, оставляем ответ модели."""
    if kind == "needs":
        qs = labels(item, "operator_questions")
        if qs is None:
            return None
        if item.get("ignored_request") or item.get("answered_by_guess") or "known_repeat" in qs:
            return "no"
        if "needed" in qs:
            return "yes"
        return "na" if criterion.get("na_allowed") else "yes"
    if kind == "consult":
        qs = labels(item, "client_questions")
        if qs is None:
            return None
        if not qs:
            return "na" if criterion.get("na_allowed") else 0
        ess = [q for q in qs if q.startswith("essential")]
        if not ess:
            return 15 if "minor_open" in qs else 20
        if "essential_wrong" in ess:
            return 0
        closed = ess.count("essential_closed")
        if closed == len(ess):
            return 15 if "minor_open" in qs else 20
        if closed >= 1:
            return 10
        if "essential_partial" in ess:
            return 5
        return 0
    if kind == "flow":
        gl = labels(item, "glitches")
        if gl is None:
            return None
        if "contradiction" in gl:
            return 0
        if len(gl) >= 2 or (gl and item.get("client_confused")):
            return 4
        return 7 if gl else 10
    return None
