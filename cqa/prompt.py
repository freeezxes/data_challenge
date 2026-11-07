"""Сборка промпта. Системная часть стабильна (кэшируется), диалог — в сообщении пользователя."""
from __future__ import annotations

from .checklist import scale_values
from .dataset import render_transcript
from .facts import extract_facts, render_facts

ANSWER_RU = {"yes": "Да", "no": "Нет", "na": "N/A"}

PREAMBLE = """Ты — эксперт контроля качества контакт-центра Freedom Broker. Оцени работу ОПЕРАТОРА в одном диалоге строго по чек-листу ниже. Твои оценки будут сравнивать с оценками опытных QA-специалистов, поэтому применяй формулировки чек-листа буквально и последовательно.

# Как читать диалог
- Реплики пронумерованы m1, m2… В квадратных скобках — время от начала диалога.
- Роли: CLIENT — клиент; OPERATOR — оператор (оцениваешь только его); BOT — бот (контекст); SYSTEM — системные события (контекст и доказательства: эскалация, подтверждение личности, закрытие чата).
- Всё, что клиент написал боту до подключения оператора, оператору известно.
- В звонках текст — автоматическая расшифровка речи с ошибками; ошибки распознавания не считаются ошибками оператора. Маски «[Имя скрыто]», «[Персональные данные скрыты]» означают, что информация прозвучала. Внутренние переговоры оператора с коллегами во время удержания клиент не слышит.
- Блок ФАКТЫ посчитан программой по меткам времени и системным событиям — опирайся на него в вопросах порядка событий и пауз. Пункты с пометкой «эвристика» проверь сам.

# Общие правила
- «Нет» — действие было нужно, но оператор его пропустил или выполнил неправильно. N/A — только если N/A разрешён для критерия и выполнено его условие неприменимости.
- Для шкалы выбери ровно одну из перечисленных ступеней.
- Критерии независимы: каждый оценивает своё. Консультация — правильно и полно ли решён запрос; ведение диалога — насколько понятно и последовательно оператор общался.
- Доказательство — ID конкретных реплик, включая системные сообщения. Для пропущенного действия укажи в checked_range участок, где оно ожидалось. Для N/A объясни, какое условие выполнено.
- reasoning — по-русски, 1–2 коротких предложения; цитаты — на языке оригинала.
- quote — дословный фрагмент одной из реплик-доказательств; выдуманная или пересказанная цитата считается ошибкой.
- Сначала найди реплики-доказательства, затем рассуждай, затем выноси ответ."""


def render_criterion(c: dict) -> str:
    if c["type"] == "scale":
        steps = "/".join(str(v) for v in scale_values(c))
        kind = f"шкала {steps}" + (" или N/A" if c.get("na_allowed") else "")
    elif c["type"] == "critical":
        kind = "Да/Нет, без баллов"
    else:
        kind = "Да/Нет" + ("/N/A" if c.get("na_allowed") else "")
    lines = [f"## {c['id']} · {c['title']} ({kind}; вес {c['weight']})"]
    if c["type"] == "scale":
        for s in sorted(c["scale"], key=lambda s: -s["value"]):
            lines.append(f"- {s['value']}: {s['text']}")
    if c.get("yes_when"):
        lines.append(f"Да — {c['yes_when']}")
    if c.get("no_when"):
        lines.append(f"Нет — {c['no_when']}")
    lines.append(f"N/A — {c['na_when']}" if c.get("na_allowed") else "N/A — не допускается.")
    for v in c.get("violations", []):
        lines.append(f"- нарушение «{v['id']}»: {v['text']}")
    if c.get("guidance"):
        lines.append("Правила толкования:")
        lines.extend(f"- {g}" for g in c["guidance"])
    return "\n".join(lines)


def render_checklist(checklist: dict) -> str:
    titles = {s["id"]: s["title"] for s in checklist.get("sections", [])}
    out, current = [f"# Чек-лист «{checklist.get('name', checklist['id'])}»"], None
    for c in checklist["criteria"]:
        if c.get("section") != current:
            current = c.get("section")
            out.append(f"\n### Секция: {titles.get(current, current)}")
        out.append(render_criterion(c))
    return "\n\n".join(out)


def render_answers(checklist: dict, answers: dict) -> str:
    parts = []
    for c in checklist["criteria"]:
        a = answers.get(c["id"])
        if a is None:
            continue
        parts.append(f"{c['id']}={ANSWER_RU.get(a, a)}")
    return "; ".join(parts)


def render_example(conv: dict, checklist: dict, gold_item: dict) -> str:
    facts = extract_facts(conv)
    return (f"### Пример {conv['conversation_id']} (канал: {conv['channel']}, язык клиента: {conv['language']})\n"
            f"ФАКТЫ:\n{render_facts(facts)}\nТРАНСКРИПТ:\n{render_transcript(conv)}\n"
            f"ЭТАЛОННАЯ ОЦЕНКА: {render_answers(checklist, gold_item['answers'])}\n"
            f"Пояснение: {gold_item.get('note') or '—'}")


def build_system_prompt(checklist: dict, examples: list[tuple[dict, dict]] | None = None) -> str:
    parts = [PREAMBLE, render_checklist(checklist)]
    if examples:
        parts.append("# Примеры размеченных диалогов (эталон QA)\n\n" +
                     "\n\n".join(render_example(conv, checklist, g) for conv, g in examples))
    return "\n\n".join(parts)


def build_user_message(conv: dict, facts: dict | None = None) -> str:
    facts = facts or extract_facts(conv)
    return (f"Оцени диалог {conv['conversation_id']} (канал: {conv['channel']}, язык клиента: {conv['language']}).\n\n"
            f"ФАКТЫ:\n{render_facts(facts)}\n\nТРАНСКРИПТ:\n{render_transcript(conv)}")
