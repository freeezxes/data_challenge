"""«Тест на чушь»: проверка, что обоснование опирается на реальные реплики, а не выдумано.

Одни и те же правила применяются к эталонной разметке и к ответам модели:
1. Доказательства указаны, и все ID реплик существуют.
2. Цитата дословно (с точностью до пробелов и регистра) есть в одной из реплик-доказательств.
3. Пояснение не пустое.
4. Доказательства согласуются со смыслом критерия:
   - «Установление контакта» — среди доказательств первая реплика оператора;
   - провал критерия (Нет / балл ниже максимума / грубая ошибка) — среди доказательств есть реплика оператора,
     а для грубой ошибки цитата взята из реплики оператора;
   - «Идентификация = Да» в чате — среди доказательств системное подтверждение личности;
   - «Завершение» — среди доказательств последняя реплика оператора или событие завершения,
     для N/A — событие обрыва.
"""
from __future__ import annotations

from .checklist import scale_values

MIN_TEXT = 12
MIN_QUOTE_LETTERS = 3


def _norm(s: str) -> str:
    return " ".join(str(s).split()).casefold()


def _kind(criterion: dict) -> str:
    title = criterion["title"].lower()
    for key, word in (("contact", "контакт"), ("identity", "идентификац"), ("closing", "завершени")):
        if word in title:
            return key
    return "other"


def is_failure(criterion: dict, answer) -> bool:
    if answer == "na":
        return False
    if criterion["type"] == "critical":
        return answer == "yes"
    if criterion["type"] == "scale":
        return int(answer) < max(scale_values(criterion))
    return answer == "no"


def check_rationale(conv: dict, facts: dict, criterion: dict, answer, rationale: dict,
                    require_quote: bool = True) -> list[str]:
    cid = criterion["id"]
    msgs = {m["id"]: m for m in conv["messages"]}
    problems: list[str] = []
    evidence = list(rationale.get("evidence") or rationale.get("evidence_ids") or [])
    quote = (rationale.get("quote") or "").strip()
    text = (rationale.get("text") or rationale.get("reasoning") or "").strip()

    if not evidence:
        problems.append(f"{cid}: нет реплик-доказательств")
    missing = [e for e in evidence if e not in msgs]
    if missing:
        problems.append(f"{cid}: несуществующие ID реплик {missing}")
    found = [msgs[e] for e in evidence if e in msgs]

    if len(text) < MIN_TEXT:
        problems.append(f"{cid}: пустое или слишком короткое пояснение")

    quote_owner = None
    if quote:
        quote_owner = next((m for m in found if _norm(quote) in _norm(m["text"])), None)
        if quote_owner is None:
            problems.append(f"{cid}: цитата «{quote[:60]}» не найдена в репликах {evidence}")
        if sum(ch.isalpha() for ch in quote) < MIN_QUOTE_LETTERS:
            problems.append(f"{cid}: цитата «{quote}» бессодержательна (цифра, символ или одно слово-междометие)")
    elif require_quote and answer != "na" and not (criterion["type"] == "critical" and answer == "no"):
        problems.append(f"{cid}: нет цитаты")

    roles = {m["role"] for m in found}
    kind = _kind(criterion)
    if kind == "contact" and facts.get("first_operator_id") and facts["first_operator_id"] not in evidence:
        problems.append(f"{cid}: среди доказательств нет первой реплики оператора {facts['first_operator_id']}")
    if is_failure(criterion, answer):
        if "operator" not in roles:
            problems.append(f"{cid}: провал критерия, но нет ни одной реплики оператора в доказательствах")
        if criterion["type"] == "critical" and quote_owner is not None and quote_owner["role"] != "operator":
            problems.append(f"{cid}: цитата грубой ошибки взята не из реплики оператора")
    if kind == "identity" and answer == "yes" and conv.get("channel") != "voice call":
        if not set(evidence) & set(facts.get("identity_confirmed_ids", [])):
            problems.append(f"{cid}: «Да», но среди доказательств нет подтверждения личности")
    if kind == "closing":
        end_ids = {e["id"] for e in facts.get("end_events", [])}
        anchors = end_ids | {facts.get("last_operator_id")}
        if answer == "na":
            cut = {e["id"] for e in facts.get("end_events", [])
                   if e["type"] in ("client_left", "connection_lost", "client_ended_call")}
            if not set(evidence) & cut:
                problems.append(f"{cid}: N/A, но среди доказательств нет события обрыва")
        elif not set(evidence) & anchors and "operator" not in roles:
            problems.append(f"{cid}: нет ни последней реплики оператора, ни события завершения")
    return problems
