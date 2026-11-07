"""Факты, которые надёжнее посчитать кодом, чем доверить модели: порядок событий и паузы.

Передаются модели подсказкой в блоке ФАКТЫ. Эвристики (приветствие, имя) помечены как
эвристики: окончательное решение остаётся за моделью.
"""
from __future__ import annotations

import re

from .dataset import format_offset

LONG_GAP_S = 180  # справочно: порог первой реакции в 3 мин в оценку не входит (стр. 2 чек-листа)

_I = re.IGNORECASE

ESCALATION = re.compile(
    r"эскалация на оператора|операторға жіберілді|transferred to an agent|"
    r"агент присоединился к разговору|agent joined", _I)
IDENTITY = re.compile(
    r"личность клиента подтверждена|жеке басы расталды|identity verified|"
    r"идентификаци\w* (успешно )?пройдена|идентификацию успешно", _I)
IDENTITY_REQUEST = re.compile(
    r"подтверд\w* личност|идентификац|проверк\w* личност|жеке бас\w* растау|растау қажет|"
    r"verify your identity|identity verification|confirm your identity", _I)
END_EVENTS = [
    ("client_left", re.compile(r"клиент покинул чат|клиент чаттан шықты|customer left|client left", _I)),
    ("connection_lost", re.compile(r"соединение с клиентом прервано|байланыс үзілді|connection to customer lost", _I)),
    ("operator_closed", re.compile(r"оператор закрыл чат|оператор чатты жапты|agent closed the chat", _I)),
    ("client_ended_call", re.compile(r"клиент завершил разговор|customer ended the call", _I)),
]
WAIT_WARNING = re.compile(
    r"сейчас провер|минут|секунд|ожида|подожд|уточню|тексеремін|күте тұр|бір минут|"
    r"one moment|let me check|please hold|bear with me|on hold", _I)
GREETING = {
    "RU": re.compile(r"здравствуй|добрый (день|вечер|утро)|доброе утро|приветствую|привет", _I),
    "KZ": re.compile(r"сәлеметсіз|сәлем|қайырлы", _I),
    "EN": re.compile(r"\b(hello|hi|hey|good (morning|afternoon|evening))\b", _I),
}
INFORMAL = re.compile(r"^\s*(привет|сәлем|hey)\b", _I)
INTRODUCTION = re.compile(
    r"меня зовут|\bя [А-ЯЁ][а-яё]+|менің атым|\bмен [А-ЯӘҒҚҢӨҰҮҺІ][а-яәғқңөұүһі]+|"
    r"\bI'?m [A-Z][a-z]+|my name is|\[имя скрыто\]", _I)


def _ids(messages, role):
    return [m for m in messages if m["role"] == role]


def extract_facts(conv: dict) -> dict:
    msgs = conv["messages"]
    by_id = {m["id"]: m for m in msgs}
    operators = _ids(msgs, "operator")
    systems = _ids(msgs, "system")
    first_op = operators[0] if operators else None
    last_op = operators[-1] if operators else None

    escalation = next((m for m in systems if ESCALATION.search(m["text"])), None)
    identity = [m["id"] for m in systems if IDENTITY.search(m["text"])]
    if conv.get("channel") == "voice call":
        identity += [m["id"] for m in operators if IDENTITY.search(m["text"])]

    identity_requested = [m["id"] for m in operators if IDENTITY_REQUEST.search(m["text"])]
    end_events = []
    for m in systems:
        for kind, rx in END_EVENTS:
            if rx.search(m["text"]):
                end_events.append({"type": kind, "id": m["id"]})
    final_event = end_events[-1] if end_events else None

    first_response_s = None
    if first_op and escalation and first_op["t"] >= escalation["t"]:
        first_response_s = first_op["t"] - escalation["t"]

    long_gaps = []
    for i, m in enumerate(msgs):
        if m["role"] != "operator" or i == 0:
            continue
        gap = m["t"] - msgs[i - 1]["t"]
        if gap >= LONG_GAP_S:
            prev_ops = [o for o in operators if o["t"] < m["t"]]
            warned = bool(prev_ops) and bool(WAIT_WARNING.search(prev_ops[-1]["text"]))
            long_gaps.append({"id": m["id"], "seconds": round(gap), "warned_before": warned})

    index = {m["id"]: i for i, m in enumerate(msgs)}
    first_op_idx = index[first_op["id"]] if first_op else len(msgs)
    known_before_operator = [m["id"] for m in msgs[:first_op_idx] if m["role"] == "client"]

    greeting = intro = informal = None
    if first_op:
        lang = (conv.get("language") or "").upper()
        rx = GREETING.get(lang)
        any_greeting = any(g.search(first_op["text"]) for g in GREETING.values())
        greeting = bool(rx.search(first_op["text"])) if rx else any_greeting
        intro = bool(INTRODUCTION.search(first_op["text"]))
        informal = bool(INFORMAL.search(first_op["text"]))

    last_msg = msgs[-1] if msgs else None
    last_op_idx = index[last_op["id"]] if last_op else None
    after_last_op = msgs[last_op_idx + 1:] if last_op else []
    before_last_op = msgs[last_op_idx - 1] if last_op and last_op_idx > 0 else None
    pending_client_question = bool(
        before_last_op and before_last_op["role"] == "client" and "?" in before_last_op["text"])

    return {
        "language": conv.get("language"),
        "channel": conv.get("channel"),
        "escalation_id": escalation["id"] if escalation else None,
        "first_operator_id": first_op["id"] if first_op else None,
        "last_operator_id": last_op["id"] if last_op else None,
        "operator_message_count": len(operators),
        "first_response_s": round(first_response_s) if first_response_s is not None else None,
        "identity_confirmed_ids": identity,
        "identity_requested_ids": identity_requested,
        "end_events": end_events,
        "final_event": final_event,
        "messages_after_last_operator": [m["id"] for m in after_last_op],
        "last_message_role": last_msg["role"] if last_msg else None,
        "client_question_before_last_operator": pending_client_question,
        "long_operator_gaps": long_gaps,
        "client_messages_before_operator": known_before_operator,
        "heuristic_first_reply": {
            "greeting_in_client_language": greeting,
            "introduced_by_name": intro,
            "informal_greeting": informal,
        },
        "_by_id": by_id,
    }


def _fmt_t(facts, mid):
    m = facts["_by_id"].get(mid)
    return f"{mid} ({format_offset(m['t'])})" if m else str(mid)


def render_facts(facts: dict) -> str:
    """Человекочитаемый блок ФАКТЫ для промпта."""
    yes = lambda v: {True: "да", False: "нет", None: "—"}[v]  # noqa: E731
    lines = []
    lines.append(f"- Эскалация/подключение оператора: {_fmt_t(facts, facts['escalation_id']) if facts['escalation_id'] else 'событие не найдено'}")
    if facts["first_operator_id"]:
        resp = facts["first_response_s"]
        resp_txt = f", через {format_offset(resp)} после эскалации" if resp is not None else ""
        lines.append(f"- Первая реплика оператора: {_fmt_t(facts, facts['first_operator_id'])}{resp_txt} (скорость первой реакции не оценивается)")
        lines.append(f"- Последняя реплика оператора: {_fmt_t(facts, facts['last_operator_id'])}")
    else:
        lines.append("- Оператор в диалоге не писал")
    if facts["client_messages_before_operator"]:
        lines.append("- Клиент написал до подключения оператора (известно оператору): " + ", ".join(facts["client_messages_before_operator"]))
    if facts["identity_confirmed_ids"]:
        lines.append("- Подтверждение личности: " + ", ".join(_fmt_t(facts, i) for i in facts["identity_confirmed_ids"]))
    else:
        lines.append("- Подтверждение личности: не найдено")
    if facts.get("identity_requested_ids"):
        lines.append("- Оператор сам говорит о проверке личности: " + ", ".join(_fmt_t(facts, i) for i in facts["identity_requested_ids"]))
    if facts["end_events"]:
        ev = ", ".join(f"{e['type']} {_fmt_t(facts, e['id'])}" for e in facts["end_events"])
        lines.append(f"- События завершения: {ev}")
    else:
        lines.append("- События завершения: нет")
    after = facts["messages_after_last_operator"]
    lines.append(f"- После последней реплики оператора: {', '.join(after) if after else 'ничего'}")
    if facts["client_question_before_last_operator"]:
        lines.append("- Перед последней репликой оператора клиент задал вопрос (проверь, ответил ли оператор)")
    gaps = facts["long_operator_gaps"]
    if gaps:
        for g in gaps:
            lines.append(f"- Пауза перед {g['id']}: {format_offset(g['seconds'])}; предупреждение об ожидании перед паузой: {yes(g['warned_before'])}")
    else:
        lines.append("- Длинных пауз перед ответами оператора нет")
    h = facts["heuristic_first_reply"]
    lines.append(
        f"- Эвристика по первой реплике оператора (проверь сам): приветствие на языке клиента={yes(h['greeting_in_client_language'])}, "
        f"имя={yes(h['introduced_by_name'])}, неформальное приветствие={yes(h['informal_greeting'])}")
    return "\n".join(lines)


def public_facts(facts: dict) -> dict:
    return {k: v for k, v in facts.items() if not k.startswith("_")}
