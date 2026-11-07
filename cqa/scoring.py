"""Детерминированный расчёт баллов по правилам чек-листа (стр. 2).

1. «Да» даёт полный вес, «Нет» — ноль; шкала — пропорционально весу (верхняя ступень = полный вес).
2. Критерии с N/A исключаются, их вес пропорционально перераспределяется: максимум остаётся 100.
3. Итог округляется до `rounding` знаков (половина — вверх). Нет применимых критериев с весом — итог None.
4. Грубые ошибки (тип critical) на баллы не влияют и отмечаются отдельно.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .checklist import normalize_answer, scale_values


def _round(value: Decimal, digits: int) -> float:
    return float(value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def raw_points(criterion: dict, answer) -> Decimal | None:
    """Баллы до перераспределения. None — критерий не участвует (N/A или без веса)."""
    weight = Decimal(str(criterion["weight"]))
    if criterion["type"] == "critical" or weight == 0 or answer == "na":
        return None
    if criterion["type"] == "scale":
        top = max(scale_values(criterion))
        return weight * Decimal(int(answer)) / Decimal(top)
    return weight if answer == "yes" else Decimal(0)


def score(checklist: dict, answers: dict) -> dict:
    digits = int(checklist.get("rounding", 2))
    rows = []
    earned = Decimal(0)
    applicable_weight = Decimal(0)
    critical = False
    violations: list[str] = []

    for c in checklist["criteria"]:
        cid = c["id"]
        if cid not in answers:
            raise ValueError(f"нет ответа по критерию {cid}")
        answer = normalize_answer(c, answers[cid])
        pts = raw_points(c, answer)
        if c["type"] == "critical" and answer == "yes":
            critical = True
            violations.append(cid)
        if pts is not None:
            earned += pts
            applicable_weight += Decimal(str(c["weight"]))
        rows.append({"id": cid, "section": c.get("section"), "answer": answer,
                     "weight": c["weight"], "raw_points": pts})

    total = None
    if applicable_weight > 0:
        total = _round(earned / applicable_weight * 100, digits)

    for r in rows:
        pts = r["raw_points"]
        r["effective_weight"] = (_round(Decimal(str(r["weight"])) / applicable_weight * 100, digits)
                                 if pts is not None and applicable_weight > 0 else None)
        r["points"] = (_round(pts / applicable_weight * 100, digits)
                       if pts is not None and applicable_weight > 0 else None)
        r["raw_points"] = _round(pts, digits) if pts is not None else None

    sections = []
    for s in checklist.get("sections", []):
        part = [r for r in rows if r["section"] == s["id"] and r["raw_points"] is not None]
        if not part:
            continue
        s_earned = sum(Decimal(str(r["raw_points"])) for r in part)
        s_weight = sum(Decimal(str(r["weight"])) for r in part)
        sections.append({
            "id": s["id"], "title": s["title"],
            "points": _round(s_earned / applicable_weight * 100, digits),
            "max_points": _round(s_weight / applicable_weight * 100, digits),
            "percent": _round(s_earned / s_weight * 100, digits) if s_weight else None,
        })

    return {
        "total": total,
        "total_reason": None if total is not None else "нет применимых критериев с весом",
        "critical": critical,
        "critical_criteria": violations,
        "criteria": rows,
        "sections": sections,
    }
