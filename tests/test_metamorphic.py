from cqa.dataset import load_records
from cqa.facts import IDENTITY
from cqa.metamorphic import build_cases, judge
from conftest import ROOT


def _records():
    return {r["conversation_id"]: r for r in load_records(ROOT / "public/dataset.json")}


def test_mutations_change_only_what_they_claim():
    recs = _records()
    cases = build_cases([recs["p1-ru-006"], recs["p1-kz-003"], recs["p1-en-006"]])
    names = {(c["base_id"], c["mutation"]) for c in cases}
    assert ("p1-ru-006", "no-identity") in names and ("p1-en-006", "no-intro") in names
    for c in cases:
        base, mut = recs[c["base_id"]]["transcript"], c["record"]["transcript"]
        if c["mutation"] == "no-identity":
            assert not any(m["speaker"] == "system" and IDENTITY.search(m["text"]) for m in mut)
        if c["mutation"] == "no-intro":
            first = next(m["text"] for m in mut if m["speaker"] == "operator")
            assert "Роман" not in first and "Sam" not in first and "Дана" not in first
        if c["mutation"] in ("insult", "bypass"):
            assert len(mut) == len(base) + 1
        assert recs[c["base_id"]]["transcript"] == base  # исходник не испорчен


def test_judge_uses_model_baseline_not_gold():
    case = {"mutation": "no-intro", "expect": {"c01": "no"}}
    base = {"c01": "yes", "c02": "na", "c04": 20}
    assert judge(case, base, {"c01": "no", "c02": "na", "c04": 20})["status"] == "pass"
    r = judge(case, base, {"c01": "no", "c02": "na", "c04": 15})
    assert r["status"] == "pass" and r["unstable"] == ["c04"]
    assert judge(case, base, {"c01": "yes"})["status"] == "fail"
    assert judge(case, {"c01": "no"}, {"c01": "no"})["status"] == "skipped"


def test_paraphrase_and_brush_off_mutations():
    from cqa.facts import extract_facts
    from cqa.dataset import normalize
    recs = _records()
    cases = build_cases([recs["p1-ru-006"], recs["p1-en-009"]], {"sys-paraphrase", "brush-off"})
    byname = {(c["base_id"], c["mutation"]): c for c in cases}
    para = byname[("p1-ru-006", "sys-paraphrase")]["record"]
    assert para["transcript"] != recs["p1-ru-006"]["transcript"]
    # факты по перефразированным сообщениям совпадают с исходными
    f0, f1 = extract_facts(normalize(recs["p1-ru-006"])), extract_facts(normalize(para))
    assert f0["identity_confirmed_ids"] == f1["identity_confirmed_ids"] and f0["escalation_id"] == f1["escalation_id"]
    f0, f1 = extract_facts(normalize(recs["p1-en-009"])), extract_facts(normalize(byname[("p1-en-009", "sys-paraphrase")]["record"]))
    assert f0["end_events"] == f1["end_events"]
    bo = byname[("p1-ru-006", "brush-off")]
    assert bo["expect"] == {"c05": "no"} and len(bo["record"]["transcript"]) == len(recs["p1-ru-006"]["transcript"]) + 2
    assert judge({"mutation": "sys-paraphrase", "expect": {}}, {"c01": "yes", "c03": "yes"}, {"c01": "yes", "c03": "no"})["unstable"] == ["c03"]
