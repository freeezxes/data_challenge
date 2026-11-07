"""Сравнение предсказаний с эталоном: точность по критериям, матрицы ошибок, отклонение итога."""
from __future__ import annotations

from collections import Counter, defaultdict

from .scoring import score


def compare(checklist: dict, gold: dict, predictions: dict[str, dict]) -> dict:
    """gold: результат load_gold; predictions: conversation_id → {criterion_id: answer}."""
    ids = [cid for cid in predictions if cid in gold["items"]]
    per_crit = {}
    for c in checklist["criteria"]:
        k = c["id"]
        hits, total, amb_hits, amb_total = 0, 0, 0, 0
        confusion: Counter = Counter()
        misses = []
        for conv_id in ids:
            g = gold["items"][conv_id]
            if k not in g["answers"] or k not in predictions[conv_id]:
                continue
            ga, pa = g["answers"][k], predictions[conv_id][k]
            total += 1
            confusion[(str(ga), str(pa))] += 1
            if ga == pa:
                hits += 1
            else:
                misses.append({"conversation_id": conv_id, "gold": ga, "pred": pa,
                               "ambiguous": k in g["ambiguous"]})
            if k in g["ambiguous"]:
                amb_total += 1
                amb_hits += int(ga == pa)
        per_crit[k] = {
            "accuracy": round(hits / total, 4) if total else None,
            "n": total,
            "accuracy_unambiguous": round((hits - amb_hits) / (total - amb_total), 4) if total - amb_total else None,
            "confusion": {f"{a}→{b}": n for (a, b), n in sorted(confusion.items())},
            "misses": misses,
        }

    totals = []
    by_lang = defaultdict(list)
    exact_all = 0
    for conv_id in ids:
        g = gold["items"][conv_id]["answers"]
        p = predictions[conv_id]
        if not all(c["id"] in p for c in checklist["criteria"]):
            continue
        gs, ps = score(checklist, g), score(checklist, p)
        if gs["total"] is not None and ps["total"] is not None:
            diff = abs(gs["total"] - ps["total"])
            totals.append(diff)
            by_lang[conv_id.split("-")[1].upper()].append(diff)
        exact_all += int(all(g[c["id"]] == p[c["id"]] for c in checklist["criteria"]))

    scored = [k for k, v in per_crit.items() if v["accuracy"] is not None]
    return {
        "n_conversations": len(ids),
        "criteria": per_crit,
        "macro_accuracy": round(sum(per_crit[k]["accuracy"] for k in scored) / len(scored), 4) if scored else None,
        "all_criteria_exact": exact_all,
        "total_mae": round(sum(totals) / len(totals), 2) if totals else None,
        "total_within_5": sum(1 for d in totals if d <= 5),
        "total_mae_by_language": {k: round(sum(v) / len(v), 2) for k, v in sorted(by_lang.items())},
    }


def format_report(report: dict) -> str:
    lines = [f"Диалогов: {report['n_conversations']}",
             f"Средняя точность по критериям: {report['macro_accuracy']}",
             f"Все критерии совпали: {report['all_criteria_exact']}",
             f"Отклонение итога (MAE): {report['total_mae']}; в пределах 5 баллов: {report['total_within_5']}",
             f"MAE по языкам: {report['total_mae_by_language']}", ""]
    for k, v in report["criteria"].items():
        lines.append(f"{k}: точность {v['accuracy']} (n={v['n']}, без спорных {v['accuracy_unambiguous']})")
        lines.append(f"    матрица: {v['confusion']}")
        for m in v["misses"]:
            flag = " [спорный]" if m["ambiguous"] else ""
            lines.append(f"    ✗ {m['conversation_id']}: эталон {m['gold']} → модель {m['pred']}{flag}")
    return "\n".join(lines)
