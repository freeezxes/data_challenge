"""Ступень считает код, а не модель.

Для «размытых» критериев модель возвращает простые факты (какие вопросы задал клиент,
закрыт ли каждый, какие были сбои, выразил ли клиент недовольство, было ли прощание),
а ответ выводится по правилам из текста чек-листа. Правила взяты из чек-листа и памятки,
а не из набора данных, поэтому переносятся на любые диалоги.

Распознавание критерия — по названию, чтобы при замене критерия в чек-листе разбор
применялся только к тем, к кому он относится.

Факты — одно строковое поле `facts` на критерий, метки через запятую: грамматика structured
outputs ограничена по размеру, и отдельные поля на каждый факт в неё не помещаются.
"""
from __future__ import annotations

import re

KINDS = {"needs": "выявление потребност", "consult": "консультац", "flow": "управление диалог",
         "empathy": "эмпати", "closing": "завершени"}

# Метки по критериям: (имя группы, множественные?) → допустимые метки.
# Списки (needs/consult/flow) — по метке на каждый вопрос или сбой, метки могут повторяться.
# Флаги (empathy/closing) — метка либо есть, либо нет.
FACTS = {
    "needs": {
        "lists": {"operator_questions": ["needed", "known_repeat"]},
        "flags": ["answered_by_guess", "ignored_request"],
        "desc": ("По одной метке на каждый вопрос оператора о ситуации клиента: needed — нужное уточнение; "
                 "known_repeat — клиент это уже сообщил (в том числе боту). Флаги: answered_by_guess — ответил по догадке "
                 "без нужного уточнения, и ответ не подошёл ситуации; ignored_request — проигнорировал суть запроса."),
    },
    "consult": {
        "lists": {"client_questions": ["essential_closed", "essential_partial", "essential_ignored", "essential_wrong",
                                       "minor_closed", "minor_open"]},
        "flags": [],
        "desc": ("По одной метке на каждый вопрос или просьбу клиента за диалог (включая заданные боту). essential — суть "
                 "обращения, minor — второстепенная деталь, о которой клиент сам спросил (то, о чём клиент не спрашивал, не "
                 "отмечай). closed — ответ или корректный следующий шаг, в том числе понятный косвенный ответ, который клиент "
                 "принял; partial — общая подсказка, клиенту неясно, как действовать; ignored — без ответа; wrong — ошибочный ответ."),
    },
    "flow": {
        "lists": {"glitches": ["missed_question", "off_topic", "unclear_wording", "confusing_order", "unwarned_wait", "contradiction"]},
        "flags": ["client_confused"],
        "desc": ("По одной метке на каждый сбой в ясности и последовательности оператора. Ошибки распознавания речи — не сбой. "
                 "Вопрос о том, что клиент уже сообщил, — не сбой ведения: это оценивает выявление потребности. "
                 "contradiction — реплики оператора противоречат друг другу так, что клиенту непонятно, что верно; если оператор "
                 "сам прямо признал ошибку и дал верные сведения, это не contradiction, а один сбой. Флаг client_confused — клиенту "
                 "пришлось переспросить, потому что он не понял сам ответ (а не потому, что вопрос пропущен)."),
    },
    "empathy": {
        "lists": {},
        "flags": ["client_upset", "addressed_problem", "rude_or_dismissive"],
        "desc": ("Флаги: client_upset — клиент выразил недовольство, беспокойство или описал неудобство (в том числе возражение "
                 "или повтор запроса с раздражением); addressed_problem — оператор отреагировал именно на эту проблему: признал, "
                 "извинился или сделал действие по сути, не дежурной фразой; rude_or_dismissive — грубость, пренебрежение, "
                 "отмахивание от вопроса или возражения, закрытие разговора вместо ответа на повторный запрос."),
    },
    "closing": {
        "lists": {},
        "flags": ["cut_off", "summary_or_next_step", "offer_help", "farewell"],
        "desc": ("Флаги: cut_off — чат оборвался до того, как оператор мог его завершить (клиент ушёл, связь прервалась, клиент "
                 "завершил звонок — по системному сообщению); summary_or_next_step — в завершающей реплике оператора есть итог "
                 "разговора или следующий шаг (не ответ на вопрос ранее); offer_help — предложение дальнейшей помощи; "
                 "farewell — оператор попрощался."),
    },
}
LABELS = {k: v for spec in FACTS.values() for k, v in spec["lists"].items()}


def kind_of(criterion: dict) -> str | None:
    title = criterion.get("title", "").lower()
    for k, word in KINDS.items():
        if word in title:
            # Fixed fact rules only describe the original answer scales. A user
            # scale is evaluated from its own definitions by the model.
            expected = {"consult": {0, 5, 10, 15, 20}, "flow": {0, 4, 7, 10}}
            if k in expected:
                if criterion.get("type") != "scale" or {s["value"] for s in criterion.get("scale", [])} != expected[k]:
                    return None
            elif criterion.get("type") != "binary":
                return None
            return k
    return None


def allowed_labels(kind: str) -> list[str]:
    spec = FACTS[kind]
    return [lab for labs in spec["lists"].values() for lab in labs] + spec["flags"]


def extra_schema(kind: str) -> tuple[dict, list[str]]:
    """Одно поле-факт `facts` для схемы ответа (строка, метки через запятую)."""
    if kind not in FACTS:
        return {}, []
    spec = FACTS[kind]
    desc = spec["desc"] + " Метки через запятую: " + ", ".join(allowed_labels(kind)) + "; пустая строка — ни одной."
    return {"facts": {"type": "string", "description": desc}}, ["facts"]


def _split(raw) -> list[str]:
    if raw is None:
        return []
    parts = raw if isinstance(raw, list) else re.split(r"[,;\s]+", str(raw))
    return [p.strip().lower() for p in parts if p and p.strip()]


def parse_facts(kind: str, item: dict) -> dict | None:
    """`facts` строкой (или старые отдельные поля) → {группа: список меток, флаг: bool}. None — фактов нет."""
    if kind not in FACTS:
        return None
    spec = FACTS[kind]
    if "facts" in item and item["facts"] is not None:
        tokens = _split(item["facts"])
        out: dict = {}
        for group, labs in spec["lists"].items():
            out[group] = [t for t in tokens if t in labs]
        for flag in spec["flags"]:
            out[flag] = flag in tokens
        return out
    # обратная совместимость: отдельные поля
    if not any(k in item for k in list(spec["lists"]) + spec["flags"]):
        return None
    out = {g: _split(item.get(g)) for g in spec["lists"]}
    for flag in spec["flags"]:
        out[flag] = bool(item.get(flag))
    return out


def unknown_labels(kind: str, item: dict) -> list[str]:
    """Метки вне допустимого списка — повод для повторного запроса."""
    if kind not in FACTS or item.get("facts") is None:
        return []
    allowed = set(allowed_labels(kind))
    return [t for t in _split(item["facts"]) if t not in allowed]


def labels(item: dict, key: str):
    """Список меток группы из строки «a, b» (или из списка). None — поля нет. Для совместимости."""
    raw = item.get(key)
    return None if raw is None else _split(raw)


def derive(kind: str, item: dict, criterion: dict):
    """Ответ из фактов по правилам чек-листа. None — фактов нет, оставляем ответ модели."""
    f = parse_facts(kind, item)
    if f is None:
        return None
    if kind == "needs":
        qs = f["operator_questions"]
        if f["ignored_request"] or f["answered_by_guess"] or "known_repeat" in qs:
            return "no"
        if "needed" in qs:
            return "yes"
        return "na" if criterion.get("na_allowed") else "yes"
    if kind == "consult":
        qs = f["client_questions"]
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
        gl = f["glitches"]
        if "contradiction" in gl:
            return 0
        if len(gl) >= 2 or (gl and f["client_confused"]):
            return 4
        return 7 if gl else 10
    if kind == "empathy":
        if f["rude_or_dismissive"]:
            return "no"
        if f["client_upset"] and not f["addressed_problem"]:
            return "no"
        return "yes"
    if kind == "closing":
        if f["cut_off"] and criterion.get("na_allowed"):
            return "na"
        return "yes" if f["farewell"] and (f["summary_or_next_step"] or f["offer_help"]) else "no"
    return None
