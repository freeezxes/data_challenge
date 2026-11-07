from cqa.checklist import criterion_map
from cqa.derive import derive, kind_of


def test_kinds_by_title(checklist):
    m = criterion_map(checklist)
    assert [kind_of(m[c]) for c in ("c02", "c04", "c06", "c01")] == ["needs", "consult", "flow", None]


def test_needs_rules(checklist):
    c = criterion_map(checklist)["c02"]
    assert derive("needs", {"operator_questions": [{"id": "m1", "kind": "needed"}]}, c) == "yes"
    assert derive("needs", {"operator_questions": [{"id": "m1", "kind": "needed"}, {"id": "m2", "kind": "known_repeat"}]}, c) == "no"
    assert derive("needs", {"operator_questions": [], "answered_by_guess": True}, c) == "no"
    assert derive("needs", {"operator_questions": []}, c) == "na"


def test_consult_rules(checklist):
    c = criterion_map(checklist)["c04"]
    q = lambda s, e=True: {"id": "m1", "essential": e, "status": s}  # noqa: E731
    assert derive("consult", {"client_questions": [q("closed")]}, c) == 20
    assert derive("consult", {"client_questions": [q("closed"), q("ignored", False)]}, c) == 15
    assert derive("consult", {"client_questions": [q("closed"), q("partial")]}, c) == 10
    assert derive("consult", {"client_questions": [q("partial")]}, c) == 5
    assert derive("consult", {"client_questions": [q("closed"), q("wrong")]}, c) == 0
    assert derive("consult", {"client_questions": []}, c) == "na"


def test_flow_rules(checklist):
    c = criterion_map(checklist)["c06"]
    g = lambda t, conf=False: {"id": "m1", "type": t, "client_confused": conf}  # noqa: E731
    assert derive("flow", {"glitches": []}, c) == 10
    assert derive("flow", {"glitches": [g("missed_question")]}, c) == 7
    assert derive("flow", {"glitches": [g("unclear_wording", True)]}, c) == 4
    assert derive("flow", {"glitches": [g("off_topic"), g("repeat_known")]}, c) == 4
    assert derive("flow", {"glitches": [g("contradiction")]}, c) == 0
