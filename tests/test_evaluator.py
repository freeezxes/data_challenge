from cqa.dataset import normalize
from cqa.evaluator import EvalConfig, Evaluator, aggregate, cost_usd
from cqa.schema import output_schema
from conftest import FakeAPI, all_answers, model_payload


def test_schema_enums_follow_checklist(checklist):
    s = output_schema(checklist)["properties"]["criteria"]["properties"]
    assert s["c01"]["properties"]["answer"]["enum"] == ["yes", "no"]
    assert s["c02"]["properties"]["answer"]["enum"] == ["yes", "no", "na"]
    assert s["c04"]["properties"]["answer"]["enum"] == ["20", "15", "10", "5", "0", "na"]
    assert s["c06"]["properties"]["answer"]["enum"] == ["10", "7", "4", "0"]
    assert s["c08"]["properties"]["violations"]["items"]["enum"] == ["insult", "bypass", "disclosure"]
    assert list(s["c01"]["properties"]) == ["evidence_ids", "checked_range", "reasoning", "answer"]


def test_evaluate_with_fake_client(checklist, chat_record):
    answers = all_answers(checklist, c03="yes", c07="na")
    api = FakeAPI([model_payload(checklist, answers)])
    ev = Evaluator(checklist, client=api, config=EvalConfig(votes=1))
    res = ev.evaluate(normalize(chat_record))
    assert res["answers"]["c04"] == 20 and res["answers"]["c07"] == "na"
    assert res["score"]["total"] == 100.0
    assert res["issues"] == []
    call = api.calls[0]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "high"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["fallbacks"] == "default"
    assert "temperature" not in call
    assert res["cost_usd"] == cost_usd("claude-opus-5-5", res["usage"])


def test_repair_on_contradiction(checklist, chat_record):
    chat_record["transcript"][4]["text"] = "Клиент написал сообщение."  # убираем подтверждение личности
    bad = model_payload(checklist, all_answers(checklist, c03="yes", c07="na"), evidence=("m99",))
    good = model_payload(checklist, all_answers(checklist, c03="no", c07="na"))
    api = FakeAPI([bad, good])
    res = Evaluator(checklist, client=api).evaluate(normalize(chat_record))
    assert len(api.calls) == 2
    assert "ВНИМАНИЕ" in api.calls[1]["messages"][0]["content"]
    assert res["answers"]["c03"] == "no" and res["issues"] == []


def test_voting(checklist):
    def run(**kw):
        return {"details": {cid: {"answer": a, "evidence_ids": [], "checked_range": "", "reasoning": ""}
                            for cid, a in all_answers(checklist, **kw).items()}}
    runs = [run(c04=15, c07="no"), run(c04=20, c07="yes"), run(c04=10, c07="no")]
    agg = aggregate(checklist, runs)
    assert agg["c04"]["answer"] == 15 and agg["c04"]["confidence"] == 0.33
    assert agg["c07"]["answer"] == "no" and agg["c07"]["confidence"] == 0.67
    assert agg["c01"]["confidence"] == 1.0


def test_cost():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}
    assert cost_usd("claude-opus-5-5", usage) == 24.2
    assert cost_usd("unknown", usage) is None
