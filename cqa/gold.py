"""Эталонная разметка DEV и политика толкования критерия 07 «Завершение»."""
from __future__ import annotations

import json
from pathlib import Path

# Тип завершения → ответ по 07 для каждой политики толкования.
C07_POLICIES = {
    # по умолчанию: «пожалуйста/рад помочь + прощание» засчитывается, голое прощание — нет
    "lenient": {"summary": "yes", "offer": "yes", "courtesy": "yes", "bare": "no", "no_bye": "no", "cut_off": "na"},
    # буквально: только итог или предложение помощи + прощание
    "strict": {"summary": "yes", "offer": "yes", "courtesy": "no", "bare": "no", "no_bye": "no", "cut_off": "na"},
    # мягко: любое прощание
    "any_farewell": {"summary": "yes", "offer": "yes", "courtesy": "yes", "bare": "yes", "no_bye": "no", "cut_off": "na"},
}


def load_gold(path: str | Path, policy: str | None = None) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    policy = policy or doc.get("c07_policy_default", "lenient")
    mapping = C07_POLICIES[policy]
    items = {}
    for item in doc["items"]:
        labels = dict(item["labels"])
        closing = labels.pop("c07_closing")
        labels["c07"] = mapping[closing]
        items[item["conversation_id"]] = {
            "answers": labels,
            "closing": closing,
            "violations": item.get("violations", []),
            "ambiguous": item.get("ambiguous", []),
            "note": item.get("note", ""),
        }
    return {"policy": policy, "items": items}
