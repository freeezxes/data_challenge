"""Чек-лист: загрузка, проверка структуры, допустимые ответы."""
from __future__ import annotations

import json
import math
from pathlib import Path

CRITERION_TYPES = ("binary", "scale", "critical")


def load_checklist(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_checklist(data)
    if errors:
        raise ValueError("Некорректный чек-лист: " + "; ".join(errors))
    return data


def validate_checklist(data: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["чек-лист должен быть объектом"]
    for field in ("id", "name"):
        if field in data and not isinstance(data[field], str):
            errors.append(f"{field} должен быть строкой")
    sections_data = data.get("sections", [])
    if not isinstance(sections_data, list) or any(not isinstance(s, dict) or not isinstance(s.get("id"), str) for s in sections_data):
        return errors + ["sections должен быть списком секций со строковыми id"]
    sections = {s["id"] for s in data.get("sections", [])}
    seen: set[str] = set()
    criteria = data.get("criteria", [])
    if not isinstance(criteria, list) or not criteria:
        return errors + ["criteria должен быть непустым списком"]
    for c in criteria:
        if not isinstance(c, dict):
            errors.append("критерий должен быть объектом")
            continue
        cid = c.get("id")
        if not isinstance(cid, str) or not cid:
            errors.append("критерий без id")
            continue
        if cid in seen:
            errors.append(f"{cid}: id повторяется")
        seen.add(cid)
        if c.get("type") not in CRITERION_TYPES:
            errors.append(f"{cid}: неизвестный тип {c.get('type')!r}")
        if sections and (not isinstance(c.get("section"), str) or c.get("section") not in sections):
            errors.append(f"{cid}: неизвестная секция {c.get('section')!r}")
        weight = c.get("weight")
        if type(weight) not in (int, float) or not math.isfinite(weight) or weight < 0:
            errors.append(f"{cid}: вес должен быть числом ≥ 0")
        if c.get("type") == "scale":
            scale = c.get("scale", [])
            steps = [s.get("value") for s in scale if isinstance(s, dict)] if isinstance(scale, list) else []
            if not isinstance(scale, list) or len(steps) != len(scale) or len(steps) < 2 or not all(type(v) is int for v in steps):
                errors.append(f"{cid}: у шкалы должно быть ≥ 2 целых ступеней")
            elif len(set(steps)) != len(steps):
                errors.append(f"{cid}: ступени шкалы повторяются")
            elif min(steps) < 0 or max(steps) <= 0:
                errors.append(f"{cid}: ступени должны быть ≥ 0, максимум — больше нуля")
        if c.get("type") == "critical" and weight:
            errors.append(f"{cid}: грубая ошибка не имеет веса")
        if c.get("na_allowed") and not c.get("na_when"):
            errors.append(f"{cid}: N/A разрешён, но не задано условие na_when")
    return errors


def criterion_map(checklist: dict) -> dict[str, dict]:
    return {c["id"]: c for c in checklist["criteria"]}


def scale_values(criterion: dict) -> list[int]:
    return sorted((s["value"] for s in criterion.get("scale", [])), reverse=True)


def allowed_answers(criterion: dict) -> list[str]:
    """Допустимые ответы как строки (для enum в JSON-схеме)."""
    if criterion["type"] == "scale":
        values = [str(v) for v in scale_values(criterion)]
    else:
        values = ["yes", "no"]
    if criterion.get("na_allowed"):
        values.append("na")
    return values


def normalize_answer(criterion: dict, answer) -> str | int:
    """Приводит ответ к каноническому виду: 'yes'/'no'/'na' или int для шкалы."""
    if isinstance(answer, str):
        answer = answer.strip().lower()
        answer = {"да": "yes", "нет": "no", "n/a": "na", "иә": "yes", "жоқ": "no"}.get(answer, answer)
    if answer == "na":
        if not criterion.get("na_allowed"):
            raise ValueError(f"{criterion['id']}: N/A не разрешён")
        return "na"
    if criterion["type"] == "scale":
        if isinstance(answer, bool) or not isinstance(answer, (int, float, str)) or (isinstance(answer, float) and (not math.isfinite(answer) or not answer.is_integer())):
            raise ValueError(f"{criterion['id']}: ступень должна быть целым числом")
        value = int(answer)
        if value not in scale_values(criterion):
            raise ValueError(f"{criterion['id']}: {value} — нет такой ступени шкалы")
        return value
    if answer not in ("yes", "no"):
        raise ValueError(f"{criterion['id']}: недопустимый ответ {answer!r}")
    return answer
