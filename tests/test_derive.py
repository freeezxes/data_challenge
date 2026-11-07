from cqa.checklist import criterion_map
from cqa.derive import derive, kind_of


def test_kinds_by_title(checklist):
    m = criterion_map(checklist)
    assert [kind_of(m[c]) for c in ("c02", "c04", "c06", "c01")] == ["needs", "consult", "flow", None]


def test_needs_rules(checklist):
    c = criterion_map(checklist)["c02"]
    assert derive("needs", {"operator_questions": ["needed"]}, c) == "yes"
    assert derive("needs", {"operator_questions": ["needed", "known_repeat"]}, c) == "no"
    assert derive("needs", {"operator_questions": [], "answered_by_guess": True}, c) == "no"
    assert derive("needs", {"operator_questions": []}, c) == "na"


def test_consult_rules(checklist):
    c = criterion_map(checklist)["c04"]
    assert derive("consult", {"client_questions": ["essential_closed"]}, c) == 20
    assert derive("consult", {"client_questions": ["essential_closed", "minor_open"]}, c) == 15
    assert derive("consult", {"client_questions": ["essential_closed", "essential_partial"]}, c) == 10
    assert derive("consult", {"client_questions": ["essential_partial"]}, c) == 5
    assert derive("consult", {"client_questions": ["essential_closed", "essential_wrong"]}, c) == 0
    assert derive("consult", {"client_questions": []}, c) == "na"


def test_flow_rules(checklist):
    c = criterion_map(checklist)["c06"]
    assert derive("flow", {"glitches": []}, c) == 10
    assert derive("flow", {"glitches": ["missed_question"]}, c) == 7
    assert derive("flow", {"glitches": ["unclear_wording"], "client_confused": True}, c) == 4
    assert derive("flow", {"glitches": ["off_topic", "missed_question"]}, c) == 4
    assert derive("flow", {"glitches": ["contradiction"]}, c) == 0


def test_labels_from_comma_string_and_unknown_flagged():
    from cqa.derive import labels, unknown_labels
    item = {"client_questions": "essential_closed, minor_open", "glitches": ""}
    assert labels(item, "client_questions") == ["essential_closed", "minor_open"]
    assert labels(item, "glitches") == [] and labels(item, "operator_questions") is None
    assert derive("consult", item, {"na_allowed": True}) == 15
    assert unknown_labels({"glitches": "missed_question, typo"}) == ["glitches: typo"]
