"""Выгрузка результатов по приложению организаторов, JSON 1.0. Оценки не изменяет."""
from __future__ import annotations

import math
import re

from .store import StoreError

CRITERIA = [
    ("c01", "contact", (True, False), 10),
    ("c02", "discovery", (True, False, None), 15),
    ("c03", "identification", (True, False, None), 20),
    ("c04", "consultation", (0, 5, 10, 15, 20, None), 20),
    ("c05", "empathy", (True, False), 15),
    ("c06", "dialogue", (0, 4, 7, 10), 15),
    ("c07", "closure", (True, False, None), 5),
    ("c08", "critical", (True, False), 0),
]


def turns(detail, size):
    ids = list(detail.get("evidence_ids") or [])
    if not ids:
        checked = str(detail.get("checked_range") or "")
        found = re.findall(r"\bm(\d+)\b", checked)
        if len(found) == 2 and re.search(r"m\d+\s*[–—-]\s*m\d+", checked):
            a, b = map(int, found)
            ids = [f"m{i}" for i in range(a, b + 1)] if 1 <= a <= b <= size else ["invalid"]
        else:
            ids = ["m" + i for i in found]
    out = []
    for mid in ids:
        hit = re.fullmatch(r"m([1-9]\d*)", str(mid))
        if not hit or not 1 <= int(hit[1]) <= size:
            raise ValueError("некорректные ссылки на реплики")
        if int(hit[1]) not in out:
            out.append(int(hit[1]))
    if not out:
        raise ValueError("не указаны реплики-доказательства или проверенный участок")
    return out


def build_submission(app, ds_id, team_name, kind, checklist_key=None):
    if kind not in ("dev", "prod"):
        raise StoreError("Выберите DEV или контрольный набор.")
    team_name = str(team_name or "").strip()
    if not team_name or len(team_name) > 160:
        raise StoreError("Укажите название команды (до 160 символов).")
    with app.store.lock:
        datasets = {d["id"]: d for d in app.store.datasets()}
        if ds_id not in datasets:
            raise StoreError("Набор не найден.")
        records = app.store.records(ds_id)
        key = checklist_key or app.store.get_setting("active_checklist")
        checklist = app.store.checklist(key)
        # Последняя оценка любой версии — только чтобы объяснить, почему готовой нет.
        latest = {**app.store.latest(ds_id), **app.store.latest_ready(ds_id, key)}
    blockers, notices = [], []
    # Состав набора — предупреждение, а не блок: если организаторы выдадут иной контроль,
    # выгрузка не должна становиться невозможной на финале.
    expected = 65 if kind == "dev" else 10
    if len(records) != expected:
        notices.append(f"Ожидалось {expected} диалогов для {kind.upper()}; в наборе {len(records)}. Проверьте, что выбран нужный набор.")
    calls = sum(r.get("channel") == "voice call" for r in records)
    if kind == "prod" and calls:
        notices.append(f"В контрольном наборе есть звонки: {calls}. Проверьте, что выбран нужный набор.")
    if kind == "dev" and (calls != 5 or len(records) - calls != 60):
        notices.append("В DEV ожидалось 60 чатов и 5 звонков.")
    cmap = {c["id"]: c for c in checklist["criteria"]}
    if set(cmap) != {c[0] for c in CRITERIA}:
        blockers.append("Для сдачи выберите версию с восемью исходными критериями организаторов.")
    else:
        for cid, code, values, weight in CRITERIA:
            c = cmap[cid]
            expected_type = "scale" if cid in ("c04", "c06") else "critical" if cid == "c08" else "binary"
            if c["type"] != expected_type or c["weight"] != weight:
                blockers.append(f"{code}: тип или вес не соответствует формату сдачи.")
            if expected_type == "scale" and {s["value"] for s in c["scale"]} != {v for v in values if v is not None}:
                blockers.append(f"{code}: шкала не соответствует формату сдачи.")

    results, models, check_rows = [], set(), []
    for record in records:
        cid = record["conversation_id"]
        ev = latest.get(cid)
        reason = None
        if not ev:
            reason = "Диалог ещё не оценён."
        elif ev["checklist_key"] != key:
            reason = "Оценка относится к другой версии чек-листа."
        elif ev["status"] not in ("ok", "needs_review") or not ev.get("result"):
            # «Проверить» — замечание к цитате хотя бы в одном прогоне; ответы по голосованию есть,
            # поэтому они выгружаются с предупреждением, а не теряются как error.
            reason = ev.get("error") or "Не удалось получить оценку."
        if ev and ev.get("result"):
            served = ev["result"].get("served_models") or [ev["result"].get("model")]
            models.update(m for m in served if isinstance(m, str) and m)
        criteria = []
        if reason is None:
            try:
                transcript = record["transcript"]
                if isinstance(transcript, str):
                    import json
                    transcript = json.loads(transcript)
                for internal, code, allowed, _ in CRITERIA:
                    answer = ev["final"]["answers"][internal]
                    value = {"yes": True, "no": False, "na": None}.get(answer, answer)
                    if not any(type(value) is type(v) and value == v for v in allowed):
                        raise ValueError(f"{code}: недопустимое значение")
                    detail = ev["result"]["criteria"][internal]
                    rationale = str(detail.get("reasoning") or "").strip()
                    if internal in ev["final"]["changed"]:
                        aid = ev["final"]["changed"][internal]
                        appeal = next(a for a in ev["appeals"] if a["id"] == aid)
                        rationale = "Решение по апелляции: " + appeal["reason"]
                    if not rationale:
                        raise ValueError(f"{code}: нет обоснования")
                    criteria.append({"criterion_id": code, "value": value, "rationale": rationale,
                                     "evidence_turns": turns(detail, len(transcript))})
                total = ev["final"]["score"]["total"]
                if isinstance(total, bool) or not isinstance(total, (int, float)) or not math.isfinite(total) or not 0 <= total <= 100:
                    raise ValueError("не рассчитан итог от 0 до 100")
                if bool(ev["final"]["score"]["critical"]) != criteria[-1]["value"]:
                    raise ValueError("флаг грубой ошибки не соответствует ответу")
            except (ValueError, KeyError, TypeError, StopIteration) as exc:
                reason = f"Оценка не готова к сдаче: {exc}"
        if reason:
            results.append({"conversation_id": cid, "status": "error", "criteria": [], "score": None, "error": reason})
        else:
            results.append({"conversation_id": cid, "status": "ok", "criteria": criteria,
                            "score": {"final_score": round(total, 2), "critical_flag": criteria[-1]["value"]}})
        warning = None
        if reason is None and ev["status"] == "needs_review":
            issues = [str(i) for i in (ev["result"].get("issues") or []) if not str(i).startswith("совет:")]
            warning = "Часть цитат не совпала дословно хотя бы в одном прогоне; выгружены ответы модели по голосованию."
            if issues:
                warning += " " + "; ".join(issues[:2])
        check_rows.append({"id": cid, "status": results[-1]["status"], "error": reason, "warning": warning})
    payload = {"schema_version": "1.0", "team_name": team_name,
               "model": {"mode": "external_api", "name": ", ".join(sorted(models)) or "Не определена: нет сохранённых ответов модели"}, "results": results}
    ready = sum(r["status"] == "ok" for r in results)
    return payload, {"dataset": ds_id, "name": datasets[ds_id]["name"], "kind": kind, "checklist": key,
                     "path": f"results/{kind}/results.json", "total": len(records), "ready": ready,
                     "errors": len(records) - ready, "warnings": sum(bool(r["warning"]) for r in check_rows),
                     "blockers": blockers, "notices": notices, "rows": check_rows, "model": payload["model"]}
