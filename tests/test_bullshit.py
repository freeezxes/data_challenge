"""Тест на чушь: обоснования эталона опираются на реальные реплики, а проверка ловит подделки."""
import json

import pytest

from cqa.checklist import criterion_map
from cqa.dataset import load_records, normalize
from cqa.facts import extract_facts
from cqa.gold import load_gold
from cqa.verify import check_rationale
from conftest import ROOT

GOLD = ROOT / "data/gold/dev_gold.json"
DATASET = ROOT / "public/dataset.json"


def test_every_gold_label_has_grounded_rationale(checklist):
    if not DATASET.exists():
        pytest.skip("нет набора с транскриптами")
    cmap = criterion_map(checklist)
    convs = {r["conversation_id"]: normalize(r) for r in load_records(DATASET)}
    raw = {i["conversation_id"]: i for i in json.loads(GOLD.read_text(encoding="utf-8"))["items"]}
    gold = load_gold(GOLD)["items"]
    problems = []
    for conv_id, item in gold.items():
        conv = convs[conv_id]
        facts = extract_facts(conv)
        rationale = raw[conv_id].get("rationale", {})
        for cid, c in cmap.items():
            if cid not in rationale:
                problems.append(f"{conv_id} {cid}: нет обоснования")
                continue
            problems += [f"{conv_id} {p}" for p in check_rationale(conv, facts, c, item["answers"][cid], rationale[cid])]
    assert not problems, "\n".join(problems[:20])


@pytest.fixture
def setup(checklist, chat_record):
    conv = normalize(chat_record)
    return conv, extract_facts(conv), criterion_map(checklist)


def test_invented_quote_is_caught(setup):
    conv, facts, cmap = setup
    bad = {"evidence": ["m4"], "quote": "Меня зовут Ольга", "text": "Поздоровался и представился."}
    assert any("не найдена" in p for p in check_rationale(conv, facts, cmap["c01"], "yes", bad))


def test_nonexistent_message_is_caught(setup):
    conv, facts, cmap = setup
    bad = {"evidence": ["m4", "m42"], "quote": "меня зовут Алина", "text": "Поздоровался и представился."}
    assert any("несуществующие" in p for p in check_rationale(conv, facts, cmap["c01"], "yes", bad))


def test_meaningless_quote_is_caught(setup):
    conv, facts, cmap = setup
    conv["messages"][0]["text"] = "2"
    bad = {"evidence": ["m1"], "quote": "2", "text": "Запрос общий, данные счёта не раскрывались."}
    assert any("бессодержательна" in p for p in check_rationale(conv, facts, cmap["c03"], "na", bad))


def test_contact_must_cite_first_operator_reply(setup):
    conv, facts, cmap = setup
    bad = {"evidence": ["m6"], "quote": "Передала вопрос", "text": "Поздоровался и представился."}
    assert any("первой реплики оператора" in p for p in check_rationale(conv, facts, cmap["c01"], "yes", bad))


def test_failure_must_cite_operator(setup):
    conv, facts, cmap = setup
    bad = {"evidence": ["m1"], "quote": "ограничение на вывод", "text": "Оператор не уточнил детали запроса."}
    assert any("реплики оператора" in p for p in check_rationale(conv, facts, cmap["c02"], "no", bad))


def test_identity_yes_needs_confirmation_event(setup):
    conv, facts, cmap = setup
    bad = {"evidence": ["m4"], "quote": "Подтвердим личность", "text": "Личность подтверждена до раскрытия."}
    assert any("подтверждения личности" in p for p in check_rationale(conv, facts, cmap["c03"], "yes", bad))
    good = {"evidence": ["m5", "m6"], "quote": "Личность клиента подтверждена.", "text": "Подтверждение раньше работы со счётом."}
    assert check_rationale(conv, facts, cmap["c03"], "yes", good) == []


def test_closing_na_needs_cut_event(setup):
    conv, facts, cmap = setup
    bad = {"evidence": ["m6"], "quote": "Передала вопрос", "text": "Диалог оборвался, завершить было нельзя."}
    assert any("события обрыва" in p for p in check_rationale(conv, facts, cmap["c07"], "na", bad))
    good = {"evidence": ["m8"], "quote": "Клиент покинул чат.", "text": "Клиент ушёл раньше прощания."}
    assert check_rationale(conv, facts, cmap["c07"], "na", good) == []
