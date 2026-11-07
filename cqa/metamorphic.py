"""Метаморфные тесты оценщика: одна контролируемая правка диалога → предсказуемая смена оценки.

Ожидание задаётся относительно ответа модели на исходный диалог, а не по эталонной разметке.
Поэтому тест не зависит от того, как размечен конкретный набор, и переносится на любые данные.

Каждая мутация меняет ровно одно свойство разговора и проверяет:
- целевой критерий изменился в ожидаемую сторону;
- остальные критерии, которые мутация не затрагивает, не сдвинулись (стабильность).
"""
from __future__ import annotations

import copy
import re

from .facts import IDENTITY

INSULT = {"RU": "Вы вообще читать умеете? Я уже всё объяснил.",
          "KZ": "Сіз оқи аласыз ба өзі? Бәрін түсіндірдім ғой.",
          "EN": "Can you even read? I already explained everything."}
BYPASS = {"RU": "Кстати, могу лично вести ваши инвестиции: переведите деньги мне на карту, через компанию оформлять не нужно.",
          "KZ": "Айтпақшы, инвестицияңызды жеке өзім жүргізе аламын: ақшаны менің картама аударыңыз, компания арқылы рәсімдеудің қажеті жоқ.",
          "EN": "By the way, I can manage your investments personally: transfer the money to my card, no need to go through the company."}
CLOSED = {"RU": "Оператор закрыл чат.", "KZ": "Оператор чатты жапты.", "EN": "Agent closed the chat."}
LEFT = {"RU": "Клиент покинул чат.", "KZ": "Клиент чаттан шықты.", "EN": "Customer left the chat."}
BARE = {"RU": "Ясно.", "KZ": "Түсінікті.", "EN": "Okay."}
INTRO = [re.compile(p, re.IGNORECASE) for p in (
    r"[,.!]?\s*(меня зовут|я)\s+[А-ЯЁ][а-яё]+[.,!]?", r"[,.!]?\s*(менің атым|мен)\s+[А-ЯӘҒҚҢӨҰҮҺІ][а-яәғқңөұүһі]+[.,!]?",
    r"[,.!]?\s*(I'?m|my name is)\s+[A-Z][a-z]+( from support)?[.,!]?")]


def _ops(t):
    return [i for i, m in enumerate(t) if m["speaker"] == "operator"]


def _clone(rec, suffix):
    r = copy.deepcopy(rec)
    r["conversation_id"] = f"{rec['conversation_id']}~{suffix}"
    return r


def drop_intro(rec):
    """Убрать имя оператора из первой реплики → c01 становится «Нет»."""
    r = _clone(rec, "no-intro"); t = r["transcript"]; ops = _ops(t)
    if not ops:
        return None
    text = t[ops[0]]["text"]
    new = text
    for rx in INTRO:
        new = rx.sub(".", new, count=1)
    if new == text:
        return None
    t[ops[0]]["text"] = new.strip(" ,")
    return r, {"c01": "no"}


def add_insult(rec):
    """Вставить оскорбление после первого ответа оператора → c08 «Да», c05 «Нет»."""
    r = _clone(rec, "insult"); t = r["transcript"]; ops = _ops(t)
    if not ops:
        return None
    i = ops[0] + 1
    t.insert(i, {"timestamp": t[ops[0]]["timestamp"], "speaker": "operator", "text": INSULT[rec["language"]]})
    return r, {"c08": "yes", "c05": "no"}


def add_bypass(rec):
    """Предложение услуги в обход компании перед последней репликой оператора → c08 «Да»."""
    r = _clone(rec, "bypass"); t = r["transcript"]; ops = _ops(t)
    if len(ops) < 2:
        return None
    t.insert(ops[-1], {"timestamp": t[ops[-1]]["timestamp"], "speaker": "operator", "text": BYPASS[rec["language"]]})
    return r, {"c08": "yes"}


def close_without_farewell(rec):
    """Последняя реплика оператора без итога и прощания + закрытие чата → c07 «Нет»."""
    r = _clone(rec, "no-farewell"); t = r["transcript"]; ops = _ops(t)
    if len(ops) < 2 or rec.get("channel") != "agent chat":
        return None
    last = ops[-1]
    t[last]["text"] = BARE[rec["language"]]
    del t[last + 1:]
    t.append({"timestamp": t[last]["timestamp"], "speaker": "system", "text": CLOSED[rec["language"]]})
    return r, {"c07": "no"}


def client_leaves(rec):
    """Клиент уходит сразу после ответа, до прощания оператора → c07 N/A."""
    r = _clone(rec, "client-left"); t = r["transcript"]; ops = _ops(t)
    if len(ops) < 2 or rec.get("channel") != "agent chat":
        return None
    del t[ops[-1]:]
    t.append({"timestamp": t[-1]["timestamp"], "speaker": "system", "text": LEFT[rec["language"]]})
    return r, {"c07": "na"}


def remove_identity(rec):
    """Убрать подтверждение личности при работе со счётом → c03 «Нет»."""
    r = _clone(rec, "no-identity"); t = r["transcript"]
    keep = [m for m in t if not (m["speaker"] == "system" and IDENTITY.search(m["text"]))]
    if len(keep) == len(t):
        return None
    r["transcript"] = keep
    return r, {"c03": "no"}


MUTATIONS = {"no-intro": drop_intro, "insult": add_insult, "bypass": add_bypass,
             "no-farewell": close_without_farewell, "client-left": client_leaves, "no-identity": remove_identity}
# Критерии, которые каждая мутация может законно задеть (их не проверяем на стабильность).
TOUCHES = {"no-intro": {"c01"}, "insult": {"c05", "c06", "c08"}, "bypass": {"c04", "c05", "c06", "c08"},
           "no-farewell": {"c04", "c05", "c06", "c07"}, "client-left": {"c04", "c06", "c07"},
           "no-identity": {"c03", "c08"}}
# Мутация осмысленна, только если на исходном диалоге модель поставила этот ответ.
PRECONDITION = {"no-intro": ("c01", "yes"), "no-identity": ("c03", "yes"),
                "insult": ("c08", "no"), "bypass": ("c08", "no"),
                "no-farewell": ("c07", None), "client-left": ("c07", None)}


def build_cases(records):
    """Все применимые мутации для набора исходных диалогов."""
    cases = []
    for rec in records:
        for name, fn in MUTATIONS.items():
            out = fn(rec)
            if out:
                mutated, expect = out
                cases.append({"base_id": rec["conversation_id"], "mutation": name, "record": mutated, "expect": expect})
    return cases


def judge(case, base_answers, mutant_answers):
    """Сравнивает ответы на мутанте с ожиданием и с ответами на исходнике."""
    crit, needed = PRECONDITION[case["mutation"]]
    if needed is not None and str(base_answers.get(crit)) != needed:
        return {"status": "skipped", "reason": f"на исходнике {crit}={base_answers.get(crit)}, мутация не информативна"}
    target = {k: (str(mutant_answers.get(k)) == v) for k, v in case["expect"].items()}
    stable = {k: str(mutant_answers.get(k)) == str(v) for k, v in base_answers.items()
              if k not in TOUCHES[case["mutation"]]}
    return {"status": "pass" if all(target.values()) else "fail", "target": target,
            "unstable": sorted(k for k, ok in stable.items() if not ok)}
