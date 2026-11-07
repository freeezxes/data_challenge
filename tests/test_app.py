"""Продуктовый контур без сети: чек-лист → оценка → апелляция → решение → пересчёт итога."""
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from app.server import App, make_handler
from app.store import StoreError, checklist_problems
from conftest import FakeAPI, all_answers, model_payload


def _wait(app, job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        job = app.store.job(job_id)
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("задание не завершилось")


@pytest.fixture
def app(tmp_path, checklist):
    payload = model_payload(checklist, all_answers(checklist))
    fake = FakeAPI([payload])
    a = App(tmp_path / "t.db", client_factory=lambda: fake, workers=2)
    a.fake = fake
    return a


def test_seed_has_dev_and_default_checklist(app):
    ds = {d["id"]: d for d in app.store.datasets()}
    assert ds["DEV"]["count"] == 65
    assert app.store.get_setting("active_checklist") == "freedom-chat-quality@v8"


def test_evaluate_appeal_decide_recalculates(app):
    job = app.start_job("DEV", ["p1-ru-001", "p1-kz-003"])
    job = _wait(app, job["id"])
    assert job["done"] == 2 and job["failed"] == 0 and job["cost_usd"] > 0
    ev = app.store.latest("DEV")["p1-ru-001"]
    assert ev["final"]["score"]["total"] == 100.0

    ap = app.store.create_appeal(ev["id"], "c04", "Клиенту не назвали срок")
    with pytest.raises(StoreError):
        app.store.create_appeal(ev["id"], "c04", "ещё раз")  # одна открытая апелляция на критерий
    assert app.store.preview(ev["id"], "c04", 10)["total"] == 90.0
    ev2 = app.store.decide(ap, "change", "Существенный вопрос остался открытым", 10)
    assert ev2["result"]["score"]["total"] == 100.0  # исходная оценка сохранена
    assert ev2["final"]["score"]["total"] == 90.0 and ev2["final"]["changed"] == {"c04": ap}
    with pytest.raises(StoreError):
        app.store.decide(ap, "confirm", "повтор")


def test_confirm_keeps_total_and_requires_reason(app):
    job = _wait(app, app.start_job("DEV", ["p1-en-004"])["id"])
    ev = app.store.latest("DEV")["p1-en-004"]
    ap = app.store.create_appeal(ev["id"], "c01", "Представился же")
    with pytest.raises(StoreError):
        app.store.decide(ap, "confirm", "  ")
    ev = app.store.decide(ap, "confirm", "Имя названо в первой реплике")
    assert ev["final"]["score"]["total"] == ev["result"]["score"]["total"] and job["done"] == 1


def test_new_checklist_version_with_replaced_criterion(app, checklist):
    body = json.loads(json.dumps(app.store.checklist()))
    body["criteria"] = [c for c in body["criteria"] if c["id"] != "c05"]
    body["criteria"].insert(4, {"id": "c09", "section": "conduct", "title": "Предложил приложение", "type": "binary",
                                "weight": 15, "na_allowed": False, "yes_when": "Оператор подсказал раздел приложения.",
                                "no_when": "Не подсказал.", "guidance": []})
    key = app.store.save_checklist(body, "замена эмпатии")
    assert key == "freedom-chat-quality@v9" and app.store.get_setting("active_checklist") == key
    bad = dict(body, criteria=[dict(c, weight=c["weight"] + 1) if c["id"] == "c09" else c for c in body["criteria"]])
    assert any("сумма весов" in p for p in checklist_problems(bad))
    with pytest.raises(StoreError):
        app.store.save_checklist(bad)
    # модель отвечает по новому набору критериев
    answers = {c["id"]: ("yes" if c["type"] == "binary" else "no" if c["type"] == "critical" else max(s["value"] for s in c["scale"]))
               for c in body["criteria"]}
    app.fake.payloads = [model_payload(app.store.checklist(key), answers)]
    job = _wait(app, app.start_job("DEV", ["p1-ru-002"])["id"])
    assert job["done"] == 1
    ev = app.store.latest("DEV")["p1-ru-002"]
    assert ev["checklist_key"] == key and "c09" in ev["result"]["answers"] and "c05" not in ev["result"]["answers"]


def test_only_missing_skips_already_evaluated(app):
    _wait(app, app.start_job("DEV", ["p1-ru-001"])["id"])
    with pytest.raises(StoreError):
        app.start_job("DEV", ["p1-ru-001"], only_missing=True)


def test_http_api_roundtrip(app):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        assert call("/api/state")[1]["has_key"] is True
        st, up = call("/api/datasets", {"name": "Контроль", "payload": {"isSuccess": True, "data": {
            "dataset_group_id": "CONTROL", "records": [json.loads(json.dumps(app.store.record("DEV", "p1-ru-001")))]}}})
        assert st == 200 and up == {"id": "CONTROL", "count": 1, "ids": ["p1-ru-001"]}
        st, job = call("/api/jobs", {"dataset_id": "CONTROL"})
        assert st == 200
        _wait(app, job["id"])
        q = call("/api/datasets/CONTROL/conversations")[1]
        assert q[0]["evaluation"]["total"] == 100.0
        st, err = call("/api/appeals", {"evaluation_id": q[0]["evaluation"]["id"], "criterion_id": "c01", "comment": ""})
        assert st == 400 and "комментарий" in err["error"]
        card = call("/api/conversations/CONTROL/p1-ru-001")[1]
        assert card["conversation"]["messages"][0]["id"] == "m1" and card["evaluations"]
        an = call("/api/analytics/CONTROL")[1]
        assert an["n_evaluated"] == 1 and an["avg_total"] == 100.0
        with urllib.request.urlopen(urllib.request.Request(base + "/recordings/p4-ru-001.mp3", headers={"Range": "bytes=0-99"})) as r:
            assert r.status == 206 and len(r.read()) == 100
        assert call("/api/nope")[0] == 404
    finally:
        srv.shutdown()


def test_no_key_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    a = App(tmp_path / "k.db")
    with pytest.raises(StoreError, match="ANTHROPIC_API_KEY"):
        a.start_job("DEV", ["p1-ru-001"])


def test_three_votes_run_in_parallel_and_report_agreement(app):
    job = _wait(app, app.start_job("DEV", ["p1-ru-003"], votes=3)["id"])
    assert job["done"] == 1
    ev = app.store.latest("DEV")["p1-ru-003"]
    # фейковые доказательства не совпадают с транскриптом → у каждого прогона есть попытка исправления
    # +1 — подготовка кэша перед параллельными вызовами
    assert ev["result"]["votes"] == 3 and len(app.fake.calls) in (4, 7)
    assert "Подготовка кэша" in app.fake.calls[0]["messages"][0]["content"]
    assert all(d["confidence"] == 1.0 and len(d["votes"]) == 3 for d in ev["result"]["criteria"].values())
    with pytest.raises(StoreError):
        app.start_job("DEV", ["p1-ru-003"], votes=9)


def test_one_failed_vote_does_not_lose_the_dialog(tmp_path, checklist):
    good = model_payload(checklist, all_answers(checklist))
    calls = {"n": 0}
    fake = FakeAPI([good])
    original = fake._create

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("сбой сети")
        return original(**kw)
    fake.messages.create = flaky
    a = App(tmp_path / "f.db", client_factory=lambda: fake, workers=1)
    _wait(a, a.start_job("DEV", ["p1-ru-001"], votes=3)["id"])
    ev = a.store.latest("DEV")["p1-ru-001"]
    assert ev["status"] != "error" and ev["result"]["votes"] == 2 and ev["result"]["votes_failed"] == 1


def test_trace_shows_prompt_model_answers_and_code_facts(app):
    _wait(app, app.start_job("DEV", ["p1-kz-003"], votes=1)["id"])
    ev = app.store.latest("DEV")["p1-kz-003"]
    from app.server import _trace, _export
    t = _trace(app, {"id": str(ev["id"])}, {}, {})
    assert "Установление контакта" in t["system_prompt"] and "ТРАНСКРИПТ" in t["user_message"]
    assert t["calls"] and json.loads(t["calls"][0]["text"])["criteria"]["c01"]["answer"] == "yes"
    assert "c02" in t["derived"] and t["facts"]
    csv_out = _export(app, {"ds": "DEV"}, {}, {})
    lines = csv_out.data.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("conversation_id;channel;language;status;c01") and len(lines) == 66
    assert any(l.startswith("p1-kz-003;") and ";100.0;" in l for l in lines)
    js = json.loads(_export(app, {"ds": "DEV"}, {"format": "json"}, {}).data)
    assert js["criteria"][0] == "c01" and len(js["results"]) == 65


def test_upload_single_transcript_and_append_keeps_order(app):
    from app.server import _upload
    one = {"conversation_id": "x-ru-001", "channel": "agent chat", "language": "RU", "audio_url": None,
           "transcript": [{"timestamp": "00:01", "speaker": "client", "text": "Здравствуйте, где выписка?"}]}
    r = _upload(app, {}, {}, {"name": "Один", "payload": json.dumps(one, ensure_ascii=False)})
    assert r["count"] == 1 and r["ids"] == ["x-ru-001"]
    r2 = _upload(app, {}, {}, {"append_to": "DEV", "payload": one})
    assert r2["id"] == "DEV"
    ids = [x["conversation_id"] for x in app.store.records("DEV")]
    assert len(ids) == 66 and ids[0] == "p1-ru-001" and ids[-1] == "x-ru-001"
    _upload(app, {}, {}, {"append_to": "DEV", "payload": one})  # повторная загрузка не дублирует
    assert len(app.store.records("DEV")) == 66
    with pytest.raises(StoreError):
        _upload(app, {}, {}, {"payload": {"foo": 1}})


def test_static_js_served_as_javascript(app):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/js/main.js") as r:
            assert r.headers["Content-Type"].startswith("text/javascript")
    finally:
        srv.shutdown()


def test_coach_variant_and_second_opinion(app, checklist):
    _wait(app, app.start_job("DEV", ["p1-ru-001"], votes=1)["id"])
    ev = app.store.latest("DEV")["p1-ru-001"]
    from app.assist import coach, make_variant, second_opinion
    from app.server import _variant

    app.fake.payloads = [{"target_id": "m10", "mode": "replace", "text": "Здравствуйте! Меня зовут Алина, сейчас помогу.", "why": "Приветствие и имя."}]
    tip = coach(app, ev["id"], "c01")
    assert tip["mode"] == "replace" and tip["original_text"] and tip["cost_usd"] > 0
    with pytest.raises(StoreError):
        coach(app, ev["id"], "c08")  # для грубой ошибки подсказки нет

    v = make_variant(app, ev["id"], "m10", "replace", tip["text"])
    rec = app.store.record("SANDBOX", v["conversation_id"])
    assert v["conversation_id"] == "p1-ru-001~1" and rec["transcript"][9]["text"] == tip["text"]
    assert rec["variant_of"]["evaluation_id"] == ev["id"] and rec["variant_of"]["edited_id"] == "m10"
    assert app.store.record("DEV", "p1-ru-001")["transcript"][9]["text"] != tip["text"]  # исходник не тронут
    with pytest.raises(StoreError):
        make_variant(app, ev["id"], "m1", "replace", "клиент так не говорил")  # чужие реплики не переписываем
    ins = make_variant(app, ev["id"], "m1", "insert_after", "Уточните, пожалуйста, номер договора.")
    assert ins["conversation_id"] == "p1-ru-001~2" and len(app.store.record("SANDBOX", ins["conversation_id"])["transcript"]) == 13

    app.fake.payloads = [model_payload(checklist, all_answers(checklist))]
    out = _variant(app, {"id": str(ev["id"])}, {}, {"target_id": "m10", "mode": "replace", "text": "Добрый день, я Алина."})
    _wait(app, out["job"]["id"])
    assert app.store.latest("SANDBOX")[out["conversation_id"]]["status"] != "error"

    ap = app.store.create_appeal(ev["id"], "c04", "Ответ был полный")
    app.fake.payloads = [{"evidence_ids": ["m10", "m99"], "quote": "Алина", "reasoning": "Ответ закрыл вопрос.",
                          "verdict": "appeal_invalid", "suggested_answer": "20"}]
    op = second_opinion(app, ap)
    assert op["verdict"] == "appeal_invalid" and op["evidence_ids"] == ["m10"]
    n = len(app.fake.calls)
    assert second_opinion(app, ap)["reasoning"] == op["reasoning"] and len(app.fake.calls) == n  # из кэша, без вызова


def test_partial_criteria_from_unfinished_json():
    from app.live import partial_criteria
    full = json.dumps({"criteria": {"c01": {"answer": "yes", "quote": 'скобка } и "кавычка"'},
                                    "c02": {"answer": "na", "evidence_ids": ["m1"]}}, "summary": "s"}, ensure_ascii=False)
    cut = full[: full.index('"c02"') + 20]
    assert list(partial_criteria(cut)) == ["c01"] and partial_criteria(cut)["c01"]["quote"].startswith("скобка }")
    assert set(partial_criteria(full)) == {"c01", "c02"}
    assert partial_criteria('{"crit') == {} and partial_criteria("") == {}


def test_live_board_shows_derived_answers(checklist):
    from app.live import LiveBoard
    b = LiveBoard()
    assert b.view("DEV", "x", checklist)["state"] == "none"
    b.start("DEV", "x", "k")
    assert b.view("DEV", "x", checklist)["state"] == "thinking"
    b.update("DEV", "x", '{"criteria": {"c04": {"client_questions": "essential_closed, minor_open", "answer": "20", "reasoning": "r"}, "c05"')
    v = b.view("DEV", "x", checklist)
    assert v["state"] == "writing" and v["criteria"]["c04"]["answer"] == 15  # ступень посчитана кодом из фактов
    b.finish("DEV", "x")
    assert b.view("DEV", "x", checklist)["state"] == "none"


def test_copilot_instant_checks_catch_violations_before_sending():
    from app.copilot import instant_checks
    msgs = [{"speaker": "client", "text": "Почему на счёте ограничение на вывод?"}]
    first = instant_checks(msgs, "Добрый день, на вашем счёте ограничение на вывод.", "RU")
    sev = {(h["criterion"], h["severity"]) for h in first}
    assert ("Идентификация", "stop") in sev and ("Установление контакта", "warn") in sev  # нет имени, данные до личности
    ok = instant_checks(msgs + [{"speaker": "system", "text": "Личность клиента подтверждена."}],
                        "Здравствуйте, меня зовут Алина.", "RU")
    assert not any(h["severity"] == "stop" for h in ok)
    assert any(h["severity"] == "stop" for h in instant_checks(msgs, "Переведите мне на карту, я лично вложу", "RU"))
    assert any(h["severity"] == "stop" for h in instant_checks(msgs, "Can you even read? I explained.", "EN"))


def test_copilot_opening_and_finish(app, checklist):
    from app.copilot import finish, hints, opening
    rec = app.store.record("DEV", "p1-ru-001")
    first, beats = opening(rec)
    assert first[0]["speaker"] == "client" and all(m["speaker"] != "operator" for b in beats for m in b)
    app.fake.payloads = [{"hints": [{"criterion": "Установление контакта", "text": "Представьтесь", "severity": "info"}],
                          "replies": [{"label": "Коротко", "text": "Здравствуйте, я Алина."}] * 4}]
    h = hints(app, first, "RU", "Алина")
    assert len(h["replies"]) == 3 and h["instant"] and app.fake.calls[-1]["model"] == "claude-haiku-5-5"
    with pytest.raises(StoreError):
        finish(app, first, "RU", "replay", {}, "Алина")
    app.fake.payloads = [model_payload(checklist, all_answers(checklist))]
    out = finish(app, first + [{"speaker": "operator", "text": "Здравствуйте, я Алина.", "timestamp": "00:40"}], "RU", "sim",
                 {"dataset_id": "DEV", "conversation_id": "p1-ru-001"}, "Алина")
    _wait(app, out["job"]["id"])
    assert app.store.record("PRACTICE", out["conversation_id"])["practice"]["mode"] == "sim"
