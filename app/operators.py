"""Карта навыков: операторы × критерии по последним оценкам, слабые места и план развития.

Имя оператора берём из его представления в первых репликах («меня зовут Алина», «мен Айбекпін»,
«I'm Sam»). В рабочей системе это поле приходит из CRM; в выданных данных его нет.
"""
from __future__ import annotations

import json
import re

from cqa.checklist import scale_values

from .copilot import _haiku

NAME = re.compile(r"(?i:меня зовут|\bя|менің атым|\bмен|my name is|\bI'?m|\bthis is)\s+([A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][a-zа-яёәғқңөұүһі]+)")
KZ_SUFFIX = re.compile(r"(пын|пін|мын|мін|бын|бін)$")
NOT_NAMES = {"Freedom", "Фридом", "Оператор", "Operator", "Сейчас", "Here", "Happy", "Sorry"}
NO_NAME = "Не представился"
HIDDEN = "Имя скрыто в записи"


def operator_name(record: dict) -> str:
    if record.get("practice", {}).get("operator"):
        return record["practice"]["operator"].strip()
    t = record["transcript"] if isinstance(record["transcript"], list) else json.loads(record["transcript"])
    for m in [m for m in t if m["speaker"] == "operator"][:2]:
        hit = NAME.search(m["text"])
        if hit and hit.group(1) not in NOT_NAMES:
            name = hit.group(1)
            if record.get("language") == "KZ" and len(name) > 5:
                name = KZ_SUFFIX.sub("", name)  # «Айбекпін» — «я Айбек»
            return name
        if "[Имя скрыто]" in m["text"]:
            return HIDDEN
    return NO_NAME


def _norm(c: dict, a):
    """Доля от максимума: да=1, нет=0, шкала — ступень/верх; N/A и грубые — нет значения."""
    if a == "na" or c["type"] == "critical":
        return None
    if c["type"] == "scale":
        return a / max(scale_values(c))
    return 1.0 if a == "yes" else 0.0


def skills(app, ds_ids: list[str], checklist_key: str) -> dict:
    checklist = app.store.checklist(checklist_key)
    crits = [c for c in checklist["criteria"]]
    rows: dict[str, dict] = {}
    team = {c["id"]: [] for c in crits}
    for ds in ds_ids:
        records = {r["conversation_id"]: r for r in app.store.records(ds)}
        for cid, ev in app.store.latest(ds).items():
            if ev["checklist_key"] != checklist_key or not ev["result"] or cid not in records:
                continue
            name = operator_name(records[cid])
            row = rows.setdefault(name, {"name": name, "dialogs": [], "scores": {c["id"]: [] for c in crits}, "critical": 0, "fails": []})
            fin = ev["final"]
            row["dialogs"].append({"dataset_id": ds, "conversation_id": cid, "total": fin["score"]["total"],
                                   "language": records[cid].get("language"), "channel": records[cid].get("channel")})
            row["critical"] += int(fin["score"]["critical"])
            for c in crits:
                a = fin["answers"][c["id"]]
                v = _norm(c, a)
                if c["type"] == "critical":
                    if a == "yes":
                        row["fails"].append({"criterion": c["id"], "dataset_id": ds, "conversation_id": cid, "value": 0,
                                             "reasoning": ev["result"]["criteria"][c["id"]].get("reasoning", "")})
                    continue
                if v is None:
                    continue
                row["scores"][c["id"]].append(v)
                team[c["id"]].append(v)
                if v < 1:
                    d = ev["result"]["criteria"][c["id"]]
                    row["fails"].append({"criterion": c["id"], "dataset_id": ds, "conversation_id": cid, "value": round(v, 2),
                                         "reasoning": d.get("reasoning", ""), "evidence": (d.get("evidence_ids") or [None])[0]})
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    out = []
    for r in rows.values():
        totals = [d["total"] for d in r["dialogs"] if d["total"] is not None]
        cells = {k: avg(v) for k, v in r["scores"].items()}
        weak = sorted(((k, v) for k, v in cells.items() if v is not None and v < 1),
                      key=lambda kv: (kv[1] - (avg(team[kv[0]]) or 0), kv[1]))
        out.append({"name": r["name"], "n": len(r["dialogs"]), "avg_total": round(sum(totals) / len(totals), 1) if totals else None,
                    "critical": r["critical"], "cells": cells, "weak": [k for k, _ in weak[:3]],
                    "dialogs": sorted(r["dialogs"], key=lambda d: d["total"] if d["total"] is not None else 101),
                    "fails": sorted(r["fails"], key=lambda f: f["value"])[:12]})
    out.sort(key=lambda r: (r["name"] in (NO_NAME, HIDDEN), r["avg_total"] if r["avg_total"] is not None else 101))
    return {"checklist_key": checklist_key,
            "criteria": [{"id": c["id"], "title": c["title"], "type": c["type"]} for c in crits],
            "team": {k: avg(v) for k, v in team.items()}, "operators": out,
            "note": "Имя оператора — из его представления в диалоге (в рабочей системе — из CRM). Диалоги без имени собраны в отдельные строки."}


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "По-русски, 1–2 предложения: главное о работе оператора."},
        "focus": {"type": "array", "items": {"type": "object", "properties": {
            "criterion": {"type": "string"},
            "why": {"type": "string", "description": "Что именно идёт не так, по примерам."},
            "practice": {"type": "string", "description": "Конкретное упражнение или привычка на неделю."},
            "phrase": {"type": "string", "description": "Пример хорошей формулировки для чата."}},
            "required": ["criterion", "why", "practice", "phrase"], "additionalProperties": False}},
    },
    "required": ["summary", "focus"], "additionalProperties": False,
}


def plan(app, ds_ids: list[str], checklist_key: str, name: str) -> dict:
    data = skills(app, ds_ids, checklist_key)
    row = next((r for r in data["operators"] if r["name"] == name), None)
    if not row:
        return {"summary": "Нет оценённых диалогов этого оператора.", "focus": []}
    titles = {c["id"]: c["title"] for c in data["criteria"]}
    p = lambda v: "не применялся" if v is None else f"{round(v * 100)}%"  # noqa: E731
    lines = [f"- {titles[f['criterion']]} (диалог {f['conversation_id']}, выполнено на {p(f['value'])}): {f['reasoning']}" for f in row["fails"]]
    cells = "; ".join(f"{titles[k]}: {p(v)} (у команды {p(data['team'][k])})" for k, v in row["cells"].items())
    system = ("Ты — наставник операторов контакт-центра Freedom Broker. По результатам оценок составь короткий план развития "
              "на неделю: 1–3 фокуса, начиная с самого важного. Опирайся только на приведённые примеры, не выдумывай случаев. "
              "Формулировки-примеры — на языке, на котором оператор ведёт чаты.")
    user = (f"Оператор: {name}. Диалогов: {row['n']}, средний итог {row['avg_total']}, грубых нарушений {row['critical']}.\n"
            f"Доля выполнения по критериям: {cells}\n\nПримеры снижений:\n" + ("\n".join(lines) or "— снижений нет"))
    out, meta = _haiku(app, system, user, PLAN_SCHEMA, max_tokens=1500)
    return {**out, **meta}
