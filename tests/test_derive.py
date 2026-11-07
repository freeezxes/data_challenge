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
    assert unknown_labels("flow", {"facts": "missed_question, typo"}) == ["typo"]
    assert unknown_labels("flow", {"glitches": "missed_question, typo"}) == []  # старый формат не проверяем


def test_single_facts_string_per_criterion():
    from cqa.derive import extra_schema, parse_facts
    assert list(extra_schema("empathy")[0]) == ["facts"] and extra_schema("contact" if False else "x") == ({}, [])
    assert parse_facts("flow", {"facts": "off_topic, client_confused"}) == {"glitches": ["off_topic"], "client_confused": True}
    assert derive("flow", {"facts": "off_topic, client_confused"}, {}) == 4
    assert derive("consult", {"facts": "essential_closed, minor_open"}, {"na_allowed": True}) == 15
    assert derive("needs", {"facts": ""}, {"na_allowed": True}) == "na"
    assert derive("empathy", {"facts": "client_upset"}, {}) == "no" and derive("empathy", {"facts": "client_upset, addressed_problem"}, {}) == "yes"
    assert derive("closing", {"facts": "farewell, offer_help"}, {"na_allowed": True}) == "yes"
    assert derive("closing", {"facts": "cut_off"}, {"na_allowed": True}) == "na"
    assert derive("closing", {"facts": "farewell"}, {"na_allowed": True}) == "no"


def test_empathy_and_closing_from_facts():
    na = {"na_allowed": True}
    assert derive("empathy", {"client_upset": False, "addressed_problem": False, "rude_or_dismissive": False}, {}) == "yes"
    assert derive("empathy", {"client_upset": True, "addressed_problem": False, "rude_or_dismissive": False}, {}) == "no"
    assert derive("empathy", {"client_upset": True, "addressed_problem": True, "rude_or_dismissive": False}, {}) == "yes"
    assert derive("empathy", {"client_upset": False, "addressed_problem": False, "rude_or_dismissive": True}, {}) == "no"
    assert derive("closing", {"cut_off": True, "farewell": False, "summary_or_next_step": False, "offer_help": False}, na) == "na"
    assert derive("closing", {"cut_off": False, "farewell": True, "summary_or_next_step": False, "offer_help": True}, na) == "yes"
    assert derive("closing", {"cut_off": False, "farewell": True, "summary_or_next_step": False, "offer_help": False}, na) == "no"
    assert derive("closing", {"cut_off": False, "farewell": False, "summary_or_next_step": True, "offer_help": True}, na) == "no"
    assert derive("closing", {"cut_off": True, "farewell": False, "summary_or_next_step": False, "offer_help": False}, {"na_allowed": False}) == "no"
    assert derive("empathy", {}, {}) is None and derive("closing", {}, na) is None
