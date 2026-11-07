import json

from cqa.checklist import criterion_map, normalize_answer
from cqa.gold import C07_POLICIES, load_gold
from cqa.metrics import compare
from cqa.scoring import score
from conftest import ROOT

GOLD = ROOT / "data/gold/dev_gold.json"


def test_gold_is_complete_and_valid(checklist):
    gold = load_gold(GOLD)
    assert len(gold["items"]) == 65
    cmap = criterion_map(checklist)
    for conv_id, item in gold["items"].items():
        assert set(item["answers"]) == set(cmap), conv_id
        for cid, ans in item["answers"].items():
            assert normalize_answer(cmap[cid], ans) == ans, (conv_id, cid)
        assert set(item["ambiguous"]) <= set(cmap), conv_id
        assert (item["answers"]["c08"] == "yes") == bool(item["violations"]), conv_id
        score(checklist, item["answers"])


def test_policies_change_only_c07():
    base = load_gold(GOLD, "lenient")["items"]
    for policy in C07_POLICIES:
        other = load_gold(GOLD, policy)["items"]
        for conv_id in base:
            a, b = dict(base[conv_id]["answers"]), dict(other[conv_id]["answers"])
            a.pop("c07"), b.pop("c07")
            assert a == b


def test_prompt_examples_are_unambiguous():
    ids = json.loads((ROOT / "config/examples.json").read_text(encoding="utf-8"))["conversation_ids"]
    gold = load_gold(GOLD)["items"]
    for i in ids:
        assert i in gold and not gold[i]["ambiguous"], i


def test_compare_perfect_and_miss(checklist):
    gold = load_gold(GOLD)
    preds = {k: dict(v["answers"]) for k, v in gold["items"].items()}
    report = compare(checklist, gold, preds)
    assert report["macro_accuracy"] == 1.0 and report["total_mae"] == 0
    preds["p1-ru-001"]["c07"] = "no"
    report = compare(checklist, gold, preds)
    assert report["criteria"]["c07"]["misses"][0]["conversation_id"] == "p1-ru-001"
    assert report["total_mae"] > 0
