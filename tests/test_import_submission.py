"""Границы импорта и официальный экспорт; без запросов к модели или Telegram."""
from copy import deepcopy
import json
import time
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import pytest

from app.server import App, _submission, _upload, make_handler
from app.store import StoreError
from app.submission import build_submission
from cqa.scoring import score


@pytest.fixture
def app(tmp_path):
    a = App(tmp_path / "import.db")
    yield a
    a.pool.shutdown(wait=True)


def record(app, cid="sample"):
    r = deepcopy(app.store.record("DEV", "p1-ru-001"))
    r["conversation_id"] = cid
    return r


def test_preview_is_read_only_and_new_import_never_merges_by_group_id(app):
    before = app.store.record("DEV", "p1-ru-001")
    changed = deepcopy(before)
    changed["transcript"][0]["text"] = "Другой запрос"
    p = app.imports.preview({"data": {"dataset_group_id": "DEV", "records": [changed]}}, "Новый DEV")
    assert len(app.store.datasets()) == 1 and not p["errors"]
    r = app.imports.commit(p["token"])
    assert r["id"] == "DEV-2" and app.store.record("DEV", before["conversation_id"]) == before
    assert app.store.record("DEV-2", changed["conversation_id"]) == changed
    with pytest.raises(StoreError, match="истёк"):
        app.imports.commit(p["token"])


def test_append_conflicts_require_explicit_skip_and_preserve_evaluations(app):
    old = app.store.record("DEV", "p1-ru-001")
    changed = deepcopy(old)
    changed["transcript"][0]["text"] = "Изменение"
    ev_id = app.store.add_evaluation("DEV", old["conversation_id"], app.store.get_setting("active_checklist"), "error", error="Старая ошибка")
    p = app.imports.preview([changed, record(app)], append_to="DEV")
    assert p["changed"] == 1 and p["new"] == 1 and p["conflicts"] == 1
    with pytest.raises(StoreError, match="совпадающие"):
        app.imports.commit(p["token"])
    r = app.imports.commit(p["token"], skip_existing=True)
    assert r["count"] == 1 and r["skipped"] == 1
    assert app.store.record("DEV", old["conversation_id"]) == old
    assert app.store.evaluation(ev_id)["error"] == "Старая ошибка"
    assert app.store.records("DEV")[-1]["conversation_id"] == "sample"


def test_validation_reports_turn_errors_and_rejects_entire_file(app):
    bad = record(app, "bad")
    del bad["transcript"][3]["speaker"]
    p = app.imports.preview([None, record(app), bad, record(app)])
    assert "token" not in p
    assert any("реплика 4" in e and "speaker" in e for e in p["errors"])
    assert any("ID повторяется" in e for e in p["errors"])
    assert len(app.store.datasets()) == 1
    with pytest.raises(StoreError, match="строка"):
        app.imports.preview('{"records":}')
    with pytest.raises(StoreError, match="total_records"):
        app.imports.preview({"records": [record(app)], "total_records": 2})


def test_expired_or_changed_preview_cannot_commit(app):
    p = app.imports.preview([record(app)], append_to="DEV")
    app.store.save_dataset("DEV", "DEV", [record(app, "concurrent")], "test")
    with pytest.raises(StoreError, match="изменился"):
        app.imports.commit(p["token"])
    p = app.imports.preview([record(app)])
    app.imports.plans[p["token"]]["created"] = time.monotonic() - 1900
    with pytest.raises(StoreError, match="истёк"):
        app.imports.commit(p["token"])


def test_organizer_api_uses_preview_and_never_overwrites_dev(app, monkeypatch):
    from app.server import _fetch_preview
    original = app.store.record("DEV", "p1-ru-001")
    monkeypatch.setenv("CQA_API_URL", "https://example.invalid/api")
    monkeypatch.setattr("app.server.fetch_dataset", lambda base, group: {
        "isSuccess": True, "data": {"dataset_group_id": group, "records": [original]}})
    p = _fetch_preview(app, {}, {}, {"group_id": "DEV"})
    assert len(app.store.datasets()) == 1 and p["source_group"] == "DEV"
    imported = app.imports.commit(p["token"])
    assert imported["id"] == "DEV-2" and app.store.record("DEV", "p1-ru-001") == original
def test_legacy_upload_cannot_bypass_protection(app):
    changed = record(app, "p1-ru-001")
    changed["transcript"][0]["text"] = "Новый текст"
    with pytest.raises(StoreError, match="другой текст"):
        _upload(app, {}, {}, {"append_to": "DEV", "payload": changed})
    assert app.store.record("DEV", "p1-ru-001")["transcript"][0]["text"] != "Новый текст"


def completed_set(app):
    records = [record(app, f"control-{i}") for i in range(10)]
    app.store.save_dataset("CONTROL", "Контроль", records, "test")
    key = app.store.get_setting("active_checklist")
    ck = app.store.checklist(key)
    answers = {"c01": "yes", "c02": "na", "c03": "na", "c04": 15, "c05": "yes", "c06": 7, "c07": "no", "c08": "yes"}
    result = {"answers": answers, "score": score(ck, answers), "model": "claude-opus-5-5",
              "criteria": {c: {"reasoning": "Обоснование оценки", "evidence_ids": ["m10"]} for c in answers}}
    for r in records:
        app.store.add_evaluation("CONTROL", r["conversation_id"], key, "ok", result)
    return result


def test_submission_matches_official_types_ids_and_score(app):
    completed_set(app)
    payload, report = build_submission(app, "CONTROL", "Наша команда", "prod")
    assert report["ready"] == 10 and not report["blockers"] and report["path"] == "results/prod/results.json"
    assert payload["schema_version"] == "1.0" and payload["model"]["mode"] == "external_api"
    criteria = {c["criterion_id"]: c for c in payload["results"][0]["criteria"]}
    assert len(criteria) == 8 and criteria["discovery"]["value"] is None
    assert criteria["contact"]["value"] is True and type(criteria["consultation"]["value"]) is int
    assert criteria["consultation"]["value"] == 15 and criteria["consultation"]["evidence_turns"] == [10]
    assert payload["results"][0]["score"]["critical_flag"] is True
    f = _submission(app, {"ds": "CONTROL"}, {"team_name": "Команда", "kind": "prod", "download": "1"}, {})
    assert f.name == "results.json" and len(json.loads(f.data)["results"]) == 10


def test_submission_includes_errors_and_requires_explicit_acknowledgement(app):
    completed_set(app)
    key = app.store.get_setting("active_checklist")
    app.store.add_evaluation("CONTROL", "control-0", key, "error", error="API недоступен")
    result = app.store.latest("CONTROL")["control-1"]["result"]
    app.store.add_evaluation("CONTROL", "control-1", key, "needs_review", result)
    payload, report = build_submission(app, "CONTROL", "Команда", "prod")
    assert report["errors"] == 1 and report["ready"] == 9 and report["warnings"] == 1
    assert payload["results"][0] == {"conversation_id": "control-0", "status": "error", "criteria": [], "score": None, "error": "API недоступен"}
    # «Проверить» не теряет оценку: ответы модели уходят в файл, в отчёте — предупреждение
    assert payload["results"][1]["status"] == "ok" and len(payload["results"][1]["criteria"]) == 8
    row = next(r for r in report["rows"] if r["id"] == "control-1")
    assert row["error"] is None and "цитат" in row["warning"]
    params = {"team_name": "Команда", "kind": "prod", "download": "1"}
    with pytest.raises(StoreError, match="явно разрешите"):
        _submission(app, {"ds": "CONTROL"}, params, {})
    f = _submission(app, {"ds": "CONTROL"}, {**params, "allow_errors": "1"}, {})
    assert len(json.loads(f.data)["results"]) == 10


def test_submission_uses_appeal_reason_and_rejects_invalid_evidence(app):
    completed_set(app)
    ev = app.store.latest("CONTROL")["control-0"]
    aid = app.store.create_appeal(ev["id"], "c04", "Пропущен основной вопрос")
    app.store.decide(aid, "change", "Существенный вопрос не закрыт", 10)
    bad = deepcopy(ev["result"])
    bad["criteria"]["c04"]["evidence_ids"] = ["m999"]
    app.store.add_evaluation("CONTROL", "control-1", ev["checklist_key"], "ok", bad)
    payload, report = build_submission(app, "CONTROL", "Команда", "prod")
    c = payload["results"][0]["criteria"][3]
    assert c["value"] == 10 and c["rationale"] == "Решение по апелляции: Существенный вопрос не закрыт"
    assert payload["results"][0]["score"]["final_score"] < ev["result"]["score"]["total"]
    assert payload["results"][1]["status"] == "error" and report["errors"] == 1


def test_submission_blocks_wrong_count_calls_and_replaced_checklist(app):
    completed_set(app)
    _, report = build_submission(app, "CONTROL", "Команда", "dev")
    assert report["blockers"]
    ck = deepcopy(app.store.checklist())
    ck["criteria"][0]["id"] = "new-contact"
    app.store.save_checklist(ck)
    _, report = build_submission(app, "CONTROL", "Команда", "prod")
    assert report["blockers"] and report["errors"] == 10
    with pytest.raises(StoreError, match="название команды"):
        build_submission(app, "CONTROL", "", "prod")


def test_http_preview_commit_and_submission_download(app):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def request(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as response:
            return response.headers, json.loads(response.read())
    try:
        _, p = request("/api/imports/preview", {"name": "Separate", "payload": record(app)})
        assert len(app.store.datasets()) == 1
        _, imported = request("/api/imports/commit", {"token": p["token"]})
        assert imported["count"] == 1 and imported["id"] != "DEV"
        completed_set(app)
        _, report = request("/api/datasets/CONTROL/submission?kind=prod&team_name=Team")
        assert report["ready"] == 10
        headers, data = request("/api/datasets/CONTROL/submission?kind=prod&team_name=Team&download=1")
        assert 'filename="results.json"' in headers["Content-Disposition"]
        assert len(data["results"]) == 10
        with pytest.raises(urllib.error.HTTPError) as error:
            request("/api/imports/preview", [])
        assert error.value.code == 400
    finally:
        srv.shutdown()
