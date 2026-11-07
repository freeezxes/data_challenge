import pytest

from cqa.scoring import score
from conftest import all_answers


def test_all_yes_gives_100(checklist):
    assert score(checklist, all_answers(checklist))["total"] == 100.0


def test_example_from_spec_na_redistribution(checklist):
    # 03 = N/A, 04 = 15/20, 07 = «Нет»: 10+15+15+15+15+0 = 70 из 80 применимых → 87.5
    s = score(checklist, all_answers(checklist, c03="na", c04=15, c07="no"))
    assert s["total"] == 87.5
    rows = {r["id"]: r for r in s["criteria"]}
    assert rows["c03"]["points"] is None
    assert rows["c04"]["raw_points"] == 15.0
    assert rows["c01"]["effective_weight"] == 12.5  # 10 / 80 × 100


def test_scale_points_proportional_to_weight(checklist):
    s = score(checklist, all_answers(checklist, c06=7))
    rows = {r["id"]: r for r in s["criteria"]}
    assert rows["c06"]["raw_points"] == 10.5  # 7/10 × 15
    assert s["total"] == 95.5


def test_rounding_half_up(checklist):
    # 02=Нет, 04=5, 07=N/A → 10+0+20+5+15+15 = 65 из 95 → 68.421… → 68.42
    s = score(checklist, all_answers(checklist, c02="no", c04=5, c07="na"))
    assert s["total"] == 68.42


def test_critical_does_not_change_total(checklist):
    s = score(checklist, all_answers(checklist, c08="yes"))
    assert s["total"] == 100.0 and s["critical"] is True and s["critical_criteria"] == ["c08"]


def test_no_applicable_criteria_gives_none():
    cl = {"rounding": 2, "sections": [], "criteria": [
        {"id": "a", "type": "binary", "weight": 10, "na_allowed": True, "na_when": "x"},
        {"id": "b", "type": "critical", "weight": 0, "na_allowed": False}]}
    s = score(cl, {"a": "na", "b": "no"})
    assert s["total"] is None and s["total_reason"]


def test_sections(checklist):
    s = score(checklist, all_answers(checklist, c03="na", c04=10))
    sec = {x["id"]: x for x in s["sections"]}
    assert sec["security"]["percent"] == 50.0  # 10 из 20
    assert "critical" not in sec


def test_invalid_answers_rejected(checklist):
    with pytest.raises(ValueError):
        score(checklist, all_answers(checklist, c04=12))
    with pytest.raises(ValueError):
        score(checklist, all_answers(checklist, c01="na"))
    with pytest.raises(ValueError):
        score(checklist, {"c01": "yes"})
