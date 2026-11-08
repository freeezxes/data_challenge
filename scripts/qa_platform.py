"""Independent product QA through real HTTP + SQLite; no harness, keys, or paid calls.

Run: .venv/bin/python scripts/qa_platform.py --output /tmp/cqa-qa.json
Each scenario gets a disposable database. Only the model SDK boundary is stubbed.
Exit 1 means an invariant failed. --serve starts a seeded UI QA instance instead.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.server import App, make_handler
from cqa.scoring import score


def record(cid="qa-chat"):
    return {"conversation_id": cid, "channel": "agent chat", "language": "RU", "transcript": [
        {"timestamp": "00:00", "speaker": "client", "text": "Здравствуйте, где узнать условия обслуживания?"},
        {"timestamp": "00:01", "speaker": "bot", "text": "Подключаю оператора."},
        {"timestamp": "00:02", "speaker": "system", "text": "Эскалация на оператора."},
        {"timestamp": "00:03", "speaker": "operator", "text": "Здравствуйте, меня зовут Алина. Условия на сайте в разделе Тарифы. Чем ещё помочь? До свидания!"},
        {"timestamp": "00:04", "speaker": "system", "text": "Личность клиента подтверждена."},
    ]}


def payload(checklist, overrides=None):
    facts = {"c02": "needed", "c04": "essential_closed", "c05": "", "c06": "", "c07": "farewell, summary_or_next_step"}
    criteria = {}
    for c in checklist["criteria"]:
        answer = str(max(s["value"] for s in c["scale"])) if c["type"] == "scale" else "no" if c["type"] == "critical" else "yes"
        d = {"answer": answer, "reasoning": "Оператор ответил на вопрос и назвал своё имя.",
             "evidence_ids": ["m4", "m5"], "checked_range": "m1–m5", "quote": "меня зовут Алина"}
        from cqa.derive import kind_of
        if kind_of(c):
            d["facts"] = facts.get(c["id"], "")
        if c["type"] == "critical":
            d["violations"] = []
        d.update((overrides or {}).get(c["id"], {}))
        criteria[c["id"]] = d
    return {"criteria": criteria, "summary": "Локальный ответ SDK для проверки логики продукта."}


class LocalModel:
    def __init__(self):
        self.app = None
        self.calls = []
        self.overrides = {}
        self.gate = None
        self.stop_reason = "end_turn"
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=self.messages)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.gate:
            self.gate.wait(5)
        schema = kwargs["output_config"]["format"]["schema"]
        if "criteria" in schema["properties"]:
            ids = set(schema["properties"]["criteria"]["properties"])
            ck = next(self.app.store.checklist(v["key"]) for v in self.app.store.checklists()
                      if set(c["id"] for c in self.app.store.checklist(v["key"])["criteria"]) == ids)
            data = payload(ck, self.overrides)
        elif "hints" in schema["properties"]:
            data = {"hints": [], "replies": [{"label": "Ответ", "text": "Здравствуйте, меня зовут Алина."}] * 3}
        elif "leaves" in schema["properties"]:
            data = {"text": "Спасибо за ответ!", "leaves": True}
        elif "target_id" in schema["properties"]:
            data = {"target_id": "m1", "mode": "replace", "text": "Здравствуйте, меня зовут Алина. Уточню ваш вопрос.", "why": "Добавлены приветствие и представление."}
        elif "verdict" in schema["properties"]:
            data = {"evidence_ids": ["m4"], "quote": "меня зовут Алина", "reasoning": "Имя прозвучало в первой реплике оператора.", "verdict": "appeal_invalid", "suggested_answer": "yes"}
        elif "focus" in schema["properties"]:
            data = {"summary": "Тренируйте установление контакта.", "focus": []}
        else:
            data = {}
        return SimpleNamespace(model=kwargs["model"], stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=json.dumps(data, ensure_ascii=False))],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=100,
                                                     cache_read_input_tokens=0, cache_creation_input_tokens=0))


@contextmanager
def platform(with_model=True):
    with tempfile.TemporaryDirectory(prefix="cqa-platform-qa-") as tmp:
        model = LocalModel()
        app = App(Path(tmp) / "qa.db", client_factory=(lambda: model) if with_model else None, workers=2)
        model.app = app
        app.store.save_dataset("QA", "QA synthetic", [record()], "qa")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def request(path, body=None, headers=None):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json", **(headers or {})})
            try:
                response = urllib.request.urlopen(req, timeout=10)
            except urllib.error.HTTPError as exc:
                response = exc
            raw = response.read()
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError):
                data = raw.decode("utf-8", errors="replace")
            return response.status, data

        try:
            yield app, model, request
        finally:
            if model.gate:
                model.gate.set()
            app.pool.shutdown(wait=True)
            srv.shutdown()
            srv.server_close()
            app.store.db.close()


def wait_job(app, jid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = app.store.job(jid)
        if job["status"] != "running":
            return job
        time.sleep(.01)
    raise TimeoutError(f"job {jid} did not finish")


def seed_result(app, ds="QA", cid="qa-chat", key=None, answers=None):
    key = key or app.store.get_setting("active_checklist")
    ck = app.store.checklist(key)
    data = payload(ck)
    a = {c: int(d["answer"]) if d["answer"].isdigit() else d["answer"] for c, d in data["criteria"].items()}
    a.update(answers or {})
    result = {"answers": a, "criteria": data["criteria"], "score": score(ck, a), "cost_usd": .01,
              "model": "local-qa", "votes": 1, "status": "ok"}
    return app.store.add_evaluation(ds, cid, key, "ok", result, source="qa-fixture")


class Suite:
    def __init__(self):
        self.results = []

    def check(self, name, expected, fn):
        try:
            passed, actual = fn()
            item = {"name": name, "expected": expected, "passed": bool(passed), "actual": actual}
        except Exception as exc:
            item = {"name": name, "expected": expected, "passed": False,
                    "actual": {"exception": type(exc).__name__, "message": str(exc)[:500]}}
        self.results.append(item)
        print(("PASS " if item["passed"] else "FAIL ") + name, flush=True)


def run_suite():
    s = Suite()

    def happy_path():
        with platform() as (app, model, req):
            code, job = req("/api/jobs", {"dataset_id": "QA", "votes": 1})
            done = wait_job(app, job["id"])
            _, card = req("/api/conversations/QA/qa-chat")
            _, stats = req("/api/analytics/QA")
            _, export = req("/api/datasets/QA/export?format=json")
            ev = card["evaluations"][0]
            return (code == 200 and done["status"] == "done" and ev["status"] == "ok"
                    and stats["avg_total"] == export["results"][0]["total"] == ev["final"]["score"]["total"],
                    {"job": done, "status": ev["status"], "total": ev["final"]["score"]["total"], "SDK_calls": len(model.calls)})
    s.check("http_evaluation_queue_analytics_export", "Real product path completes and totals agree", happy_path)

    def import_rules():
        with platform(False) as (app, _, req):
            original = app.store.record("QA", "qa-chat")
            bad = record("bad"); bad["transcript"][0]["timestamp"] = "00:99"
            _, invalid = req("/api/imports/preview", {"payload": [record("new"), bad]})
            _, p = req("/api/imports/preview", {"payload": [record("new")], "append_to": "QA"})
            _, committed = req("/api/imports/commit", {"token": p["token"]})
            replay, _ = req("/api/imports/commit", {"token": p["token"]})
            changed = record(); changed["transcript"][0]["text"] = "changed"
            _, p2 = req("/api/imports/preview", {"payload": [changed], "append_to": "QA"})
            conflict, _ = req("/api/imports/commit", {"token": p2["token"]})
            _, skip = req("/api/imports/commit", {"token": p2["token"], "skip_existing": True})
            return ("token" not in invalid and committed["count"] == 1 and replay == conflict == 400
                    and skip["skipped"] == 1 and app.store.record("QA", "qa-chat") == original,
                    {"invalid": invalid["errors"], "committed": committed, "replay": replay, "skip": skip})
    s.check("import_atomic_validation_conflicts_one_use_token", "Reject entire malformed import; never overwrite transcripts", import_rules)

    def stale_preview():
        with platform(False) as (_, _, req):
            _, a = req("/api/imports/preview", {"payload": record("a"), "append_to": "QA"})
            _, b = req("/api/imports/preview", {"payload": record("b"), "append_to": "QA"})
            req("/api/imports/commit", {"token": b["token"]})
            code, data = req("/api/imports/commit", {"token": a["token"]})
            return code == 400, {"code": code, "body": data}
    s.check("import_stale_preview", "Reject append if dataset changed after preview", stale_preview)

    def appeals():
        with platform(False) as (app, _, req):
            eid = seed_result(app, answers={"c03": "no"})
            _, ap = req("/api/appeals", {"evaluation_id": eid, "criterion_id": "c03", "comment": "Проверьте идентификацию"})
            duplicate, _ = req("/api/appeals", {"evaluation_id": eid, "criterion_id": "c03", "comment": "Ещё раз"})
            _, preview = req(f"/api/evaluations/{eid}/preview", {"criterion_id": "c03", "new_answer": "na"})
            _, ev = req(f"/api/appeals/{ap['id']}/decision", {"action": "change", "reason": "Общий вопрос", "new_answer": "na"})
            again, _ = req(f"/api/appeals/{ap['id']}/decision", {"action": "confirm", "reason": "повтор"})
            _, stats = req("/api/analytics/QA")
            _, export = req("/api/datasets/QA/export?format=json")
            return (duplicate == again == 400 and ev["result"]["answers"]["c03"] == "no"
                    and ev["final"]["answers"]["c03"] == "na" and preview["total"] == stats["avg_total"] == export["results"][0]["total"],
                    {"original": ev["result"]["score"]["total"], "final": preview["total"], "duplicate": duplicate, "repeat_decision": again})
    s.check("appeal_na_preview_immutability_aggregates", "Appeal recalculates N/A consistently; original stays immutable", appeals)

    def invalid_job(votes):
        with platform() as (app, _, req):
            code, data = req("/api/jobs", {"dataset_id": "QA", "votes": votes})
            jobs = app.store.jobs()
            return code == 400 and not jobs, {"code": code, "response": data, "persisted_jobs": jobs}
    for v in (6, "bad", 0):
        s.check(f"invalid_job_votes_{v}", "400 and no persistent job for invalid votes", lambda v=v: invalid_job(v))

    def duplicate_jobs():
        with platform() as (app, model, req):
            model.gate = threading.Event()
            c1, j1 = req("/api/jobs", {"dataset_id": "QA", "votes": 1})
            c2, j2 = req("/api/jobs", {"dataset_id": "QA", "votes": 1, "only_missing": True})
            model.gate.set()
            wait_job(app, j1["id"])
            if c2 == 200: wait_job(app, j2["id"])
            evs = app.store.evaluations("QA", "qa-chat")
            return len(evs) == 1, {"statuses": [c1, c2], "evaluations": len(evs), "SDK_calls": len(model.calls)}
    s.check("running_conversation_not_evaluated_twice", "Second click/only_missing does not duplicate an in-flight paid evaluation", duplicate_jobs)

    def restart():
        with platform(False) as (app, _, _):
            jid = app.store.create_job("QA", app.store.get_setting("active_checklist"), 1)
            reopened = App(app.store.path)
            try:
                j = reopened.store.job(jid)
                return j["status"] != "running", j
            finally:
                reopened.pool.shutdown(); reopened.store.db.close()
    s.check("restart_recovers_abandoned_jobs", "Restart marks interrupted jobs terminal or resumes them", restart)

    def long_running_hidden():
        with platform(False) as (app, _, req):
            key = app.store.get_setting("active_checklist")
            first = app.store.create_job("QA", key, 1)
            for _ in range(20):
                jid = app.store.create_job("QA", key, 1)
                app.store.finish_job(jid)
            _, jobs = req("/api/jobs")
            return any(j["id"] == first for j in jobs), {"running_job": first, "returned_jobs": len(jobs), "running_returned": sum(j["status"] == "running" for j in jobs)}
    s.check("polling_cannot_hide_older_running_job", "Polling list always exposes running work even after 20 newer completed jobs", long_running_hidden)

    def practice_collision():
        with platform() as (app, _, req), patch("app.copilot.time.strftime", return_value="qa-fixed-second"):
            ids = []
            for text in ("Первая тренировка", "Вторая тренировка"):
                _, data = req("/api/copilot/finish", {"messages": [{"speaker": "operator", "text": text, "timestamp": "00:00"}], "language": "RU"})
                wait_job(app, data["job"]["id"])
                ids.append(data["conversation_id"])
            return len(set(ids)) == 2 and len(app.store.records("PRACTICE")) == 2, {"ids": ids, "stored_records": len(app.store.records("PRACTICE")), "evaluations": len(app.store.evaluations("PRACTICE", ids[0]))}
    s.check("practice_unique_ids_same_second", "Two completed chats in the same second preserve both transcripts", practice_collision)

    def no_key_finish():
        with platform(False) as (app, _, req):
            code, data = req("/api/copilot/finish", {"messages": [{"speaker": "operator", "text": "Здравствуйте"}], "language": "RU"})
            return code == 400 and not app.store.records("PRACTICE"), {"code": code, "response": data, "saved_chats_despite_error": len(app.store.records("PRACTICE"))}
    s.check("practice_no_key_atomic_failure", "Rejected finish must not create an undisclosed saved chat", no_key_finish)

    def version_views():
        with platform(False) as (app, _, req):
            v1 = app.store.get_setting("active_checklist"); seed_result(app, key=v1)
            ck = app.store.checklist(v1); ck["name"] += " v2"
            v2 = app.store.save_checklist(ck); seed_result(app, key=v2)
            _, analytics = req("/api/analytics/QA?checklist=" + urllib.parse.quote(v1))
            _, export = req("/api/datasets/QA/export?format=json&checklist=" + urllib.parse.quote(v1))
            _, ops = req("/api/operators?ds=QA&checklist=" + urllib.parse.quote(v1))
            return analytics["n_evaluated"] == 1 and export["results"][0]["status"] == "ok" and len(ops["operators"]) == 1, {"v1": v1, "v2": v2, "v1_analytics_evaluated": analytics["n_evaluated"], "v1_export_status": export["results"][0]["status"], "v1_operators": len(ops["operators"])}
    s.check("explicit_historical_version_views", "Selecting v1 returns its saved result even after v2 evaluation", version_views)

    def counts_after_error():
        with platform(False) as (app, _, req):
            seed_result(app)
            app.store.add_evaluation("QA", "qa-chat", app.store.get_setting("active_checklist"), "error", error="API unavailable")
            _, st = req("/api/state"); _, queue = req("/api/datasets/QA/conversations")
            n = next(d["evaluated"] for d in st["datasets"] if d["id"] == "QA")
            ready = sum(bool(r["evaluation"] and r["evaluation"]["status"] != "error") for r in queue)
            return n == ready, {"state_evaluated": n, "queue_evaluated": ready, "latest_status": queue[0]["evaluation"]["status"]}
    s.check("state_counters_match_latest_evaluation", "Dataset counters and queue use the same latest-result semantics", counts_after_error)

    def scale_edit():
        with platform() as (app, _, req):
            ck = app.store.checklist(); c = next(c for c in ck["criteria"] if c["id"] == "c04")
            c["scale"] = [{"value": v, "text": str(v)} for v in (100, 50, 0)]
            code, saved = req("/api/checklists", {"body": ck})
            _, job = req("/api/jobs", {"dataset_id": "QA", "votes": 1, "checklist_key": saved["key"]})
            done = wait_job(app, job["id"])
            ev = app.store.evaluations("QA", "qa-chat")[0]
            return code == 200 and done["status"] == "done", {"saved": code, "job": done["status"], "evaluation_error": ev["error"]}
    s.check("editable_consultation_scale_evaluates", "Saved 0/50/100 scale works through normal evaluator", scale_edit)

    def zero_top_scale():
        with platform(False) as (app, _, req):
            ck = app.store.checklist(); next(c for c in ck["criteria"] if c["id"] == "c04")["scale"] = [{"value": -1, "text": "bad"}, {"value": 0, "text": "good"}]
            code, data = req("/api/checklists", {"body": ck})
            details = {"save_code": code, "activated": data.get("key")}
            if code == 200:
                try:
                    seed_result(app, key=data["key"])
                except Exception as exc:
                    details["scoring_exception"] = type(exc).__name__ + ": " + str(exc)
            return code == 400, details
    s.check("reject_scale_with_zero_maximum", "Unscorable scale rejected before activation", zero_top_scale)

    def fraction_answer():
        with platform(False) as (app, _, req):
            eid = seed_result(app)
            _, ap = req("/api/appeals", {"evaluation_id": eid, "criterion_id": "c06", "comment": "Проверка ступени"})
            code, data = req(f"/api/appeals/{ap['id']}/decision", {"action": "change", "reason": "Некорректная ступень", "new_answer": 7.9})
            return code == 400, {"code": code, "saved_answer": data.get("final", {}).get("answers", {}).get("c06")}
    s.check("reject_fractional_scale_decision", "7.9 is rejected rather than silently truncated to 7", fraction_answer)

    def race_appeal(decision=False):
        with platform(False) as (app, _, req):
            eid = seed_result(app)
            body = {"evaluation_id": eid, "criterion_id": "c01", "comment": "Параллельная апелляция"}
            barrier = threading.Barrier(2)
            if decision:
                _, ap = req("/api/appeals", body)
                original = app.store.appeal
                def synced(aid):
                    out = original(aid)
                    barrier.wait(timeout=5)
                    return out
                target, fn = "appeal", synced
                path = f"/api/appeals/{ap['id']}/decision"
                bodies = [{"action": "change", "new_answer": "no", "reason": "Reviewer A"}, {"action": "confirm", "reason": "Reviewer B"}]
            else:
                original = app.store.evaluation
                def synced(aid):
                    out = original(aid)
                    barrier.wait(timeout=5)
                    return out
                target, fn = "evaluation", synced
                path, bodies = "/api/appeals", [body, body]
            with patch.object(app.store, target, fn), ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda b: req(path, b), bodies))
            codes = [r[0] for r in results]
            return codes.count(200) == 1, {"codes": codes, "appeals": app.store.appeals()}
    s.check("concurrent_duplicate_appeals", "Only one open appeal per evaluation and criterion", lambda: race_appeal(False))
    s.check("concurrent_reviewer_decisions", "Only first decision succeeds; second cannot overwrite audit trail", lambda: race_appeal(True))

    malformed = [
        ("checklist_null", "/api/checklists", {"body": None}),
        ("checklist_weight_string", "/api/checklists", "weight"),
        ("appeal_comment_object", "/api/appeals", "comment"),
        ("copilot_invalid_messages", "/api/copilot/check", {"messages": [None], "draft": "hello"}),
        ("copilot_draft_object", "/api/copilot/check", {"messages": [], "draft": {"text": "hello"}}),
    ]
    for name, path, body in malformed:
        def invalid(path=path, body=body):
            with platform(False) as (app, _, req):
                if body == "weight":
                    ck = app.store.checklist(); ck["criteria"][0]["weight"] = "10"; body = {"body": ck}
                elif body == "comment":
                    body = {"evaluation_id": seed_result(app), "criterion_id": "c01", "comment": {"x": 1}}
                code, data = req(path, body)
                return code == 400, {"code": code, "body": data}
        s.check(name, "Invalid JSON field types return 400, never 500", invalid)

    def reverse_time():
        with platform(False) as (_, _, req):
            r = record(); r["transcript"][3]["timestamp"] = "00:00"
            _, p = req("/api/imports/preview", {"payload": r})
            return bool(p["errors"]), {"errors": p["errors"], "has_commit_token": "token" in p}
    s.check("reject_backwards_timestamps", "Out-of-order timestamps rejected before timing/facts calculations", reverse_time)

    def dataset_audio():
        with platform(False) as (app, _, req):
            cid = next(k for k in __import__("app.server", fromlist=["RECORDINGS"]).RECORDINGS)
            r = record(cid); r["channel"] = "voice call"; r["audio_url"] = "https://example.invalid/own.wav"
            app.store.save_dataset("AUDIO-QA", "Other dataset", [r], "qa")
            _, card = req("/api/conversations/AUDIO-QA/" + urllib.parse.quote(cid))
            return card["audio"] == r["audio_url"], {"requested_audio": r["audio_url"], "actual_audio": card["audio"]}
    s.check("recording_scoped_to_dataset", "Imported audio wins over unrelated DEV recording with same conversation ID", dataset_audio)

    def paid_failure():
        with platform() as (app, model, req):
            model.stop_reason = "refusal"
            _, job = req("/api/jobs", {"dataset_id": "QA", "votes": 1})
            done = wait_job(app, job["id"])
            eid = app.store.evaluations("QA", "qa-chat")[0]["id"]
            _, trace = req(f"/api/evaluations/{eid}/trace")
            return done["cost_usd"] > 0 and len(trace["calls"]) == 1, {"SDK_calls_with_usage": len(model.calls), "job_cost": done["cost_usd"], "trace_calls": len(trace["calls"]), "trace_usage": trace["usage"]}
    s.check("failed_model_call_keeps_usage_cost_and_trace", "Billed SDK response remains traceable even when model refuses", paid_failure)

    def costs():
        with platform() as (app, _, req):
            _, job = req("/api/jobs", {"dataset_id": "QA", "votes": 3})
            done = wait_job(app, job["id"])
            _, analytics = req("/api/analytics/QA")
            return abs(analytics["cost_usd"] - done["cost_usd"]) < .000001, {"job_total": done["cost_usd"], "analytics_total": analytics["cost_usd"]}
    s.check("analytics_total_cost_includes_cache_warmup", "Analytics total spent includes cache preparation billed in job", costs)

    def critical_after_appeal():
        with platform(False) as (app, _, req):
            eid = seed_result(app, answers={"c08": "yes"})
            ev = app.store.evaluation(eid); result = ev["result"]
            result["criteria"]["c08"]["violations"] = ["disclosure"]
            app.store.x("update evaluations set result=? where id=?", (json.dumps(result), eid))
            aid = app.store.create_appeal(eid, "c08", "Раскрытия не было")
            app.store.decide(aid, "change", "Сведения были общими", "no")
            _, queue = req("/api/datasets/QA/conversations")
            e = queue[0]["evaluation"]
            return not e["critical"] and not e["violations"], {"critical": e["critical"], "violations": e["violations"]}
    s.check("withdrawn_critical_violation_removed_from_queue", "Final queue does not show a violation overturned on appeal", critical_after_appeal)

    def variant_race():
        with platform() as (app, _, req):
            eid = seed_result(app)
            app.store.save_dataset("SANDBOX", "Sandbox", [record("placeholder")], "qa")
            original = app.store.records; barrier = threading.Barrier(2); seen = threading.local()
            def synced(ds):
                rows = original(ds)
                if ds == "SANDBOX" and not getattr(seen, "passed", False):
                    seen.passed = True
                    barrier.wait(timeout=5)
                return rows
            with patch.object(app.store, "records", synced), ThreadPoolExecutor(2) as pool:
                responses = list(pool.map(lambda text: req(f"/api/evaluations/{eid}/variant", {
                    "target_id": "m4", "mode": "replace", "text": text, "votes": 1}),
                    ["Здравствуйте, меня зовут Алина. Variant A", "Здравствуйте, меня зовут Алина. Variant B"]))
            for code, body in responses:
                if code == 200: wait_job(app, body["job"]["id"])
            records = [r for r in app.store.records("SANDBOX") if r["conversation_id"] != "placeholder"]
            return len(records) == 2, {"responses": [r[0] for r in responses], "variants": [(r["conversation_id"], r["transcript"][3]["text"]) for r in records]}
    s.check("concurrent_variants_do_not_overwrite", "Concurrent edits allocate distinct immutable variant IDs", variant_race)

    def scoring_properties():
        from decimal import Decimal, ROUND_HALF_UP
        from itertools import product
        with platform(False) as (app, _, _):
            ck = app.store.checklist()
            values = [("yes", "no", "na") if c.get("na_allowed") and c["type"] != "scale" else
                      tuple([x["value"] for x in c["scale"]] + (["na"] if c.get("na_allowed") else [])) if c["type"] == "scale" else
                      ("yes", "no") for c in ck["criteria"]]
            n = 0
            for comb in product(*values):
                answers = dict(zip([c["id"] for c in ck["criteria"]], comb))
                earned, weight = Decimal(0), Decimal(0)
                for c, a in zip(ck["criteria"], comb):
                    if c["type"] == "critical" or a == "na" or not c["weight"]: continue
                    w = Decimal(str(c["weight"])); weight += w
                    earned += w * Decimal(a) / Decimal(max(x["value"] for x in c["scale"])) if c["type"] == "scale" else w if a == "yes" else 0
                want = float((100 * earned / weight).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) if weight else None
                actual = score(ck, answers)
                if actual["total"] != want or actual["critical"] != (answers["c08"] == "yes"):
                    return False, {"answers": answers, "expected": want, "actual": actual}
                n += 1
            return True, {"combinations": n}
    s.check("exhaustive_default_scoring_independent_decimal_oracle", "Every allowed default answer combination matches an independent decimal calculation", scoring_properties)

    def needs_review():
        with platform() as (app, model, req):
            model.overrides = {"c01": {"quote": "This quote never occurs in the transcript"}}
            _, job = req("/api/jobs", {"dataset_id": "QA", "votes": 1})
            wait_job(app, job["id"])
            ev = app.store.evaluations("QA", "qa-chat")[0]
            return ev["status"] == "needs_review" and len(model.calls) == 2, {"status": ev["status"], "issues": ev["result"]["issues"], "SDK_calls_including_repair": len(model.calls)}
    s.check("invalid_quote_repair_then_needs_review", "Invalid proof gets one repair, then remains explicitly unverified", needs_review)

    def version_immutability():
        with platform(False) as (app, _, req):
            eid = seed_result(app); old = app.store.evaluation(eid)
            ck = app.store.checklist(); ck["criteria"][0]["weight"] = 20; ck["criteria"][1]["weight"] = 5
            code, new = req("/api/checklists", {"body": ck})
            after = app.store.evaluation(eid)
            return code == 200 and after == old and new["key"] != old["checklist_key"], {"old_key": old["checklist_key"], "new_key": new["key"], "original_unchanged": after == old}
    s.check("saving_checklist_does_not_mutate_history", "New weights leave historical evaluation and score unchanged", version_immutability)

    def malicious_strings():
        with platform(False) as (_, _, req):
            cid = "qa/<img src=x onerror=alert(1)>?&%#"
            r = record(cid)
            _, p = req("/api/imports/preview", {"name": "<script>bad</script>", "payload": r})
            _, imp = req("/api/imports/commit", {"token": p["token"]})
            code, card = req("/api/conversations/" + urllib.parse.quote(imp["id"], safe="") + "/" + urllib.parse.quote(cid, safe=""))
            return code == 200 and card["conversation"]["conversation_id"] == cid, {"code": code, "exact_ID_preserved": card.get("conversation", {}).get("conversation_id") == cid}
    s.check("encoded_unicode_special_character_ids", "Imported special-character IDs remain addressable through API", malicious_strings)

    def blocked_paths():
        with platform(False) as (_, _, req):
            results = {}
            for p in ("/qa-missing-file", "/.env", "/../app/store.py", "/%2e%2e/app/store.py", "/app/store.py"):
                try:
                    results[p] = {"code": req(p)[0]}
                except Exception as exc:
                    results[p] = {"exception": type(exc).__name__, "message": str(exc)}
            return all(r.get("code") == 404 for r in results.values()), results
    s.check("missing_static_and_non_public_paths_return_404", "Missing/forbidden static paths return valid 404 responses", blocked_paths)

    def no_operator():
        with platform(False) as (app, _, req):
            code, data = req("/api/copilot/finish", {"messages": [{"speaker": "client", "text": "Hello"}], "language": "EN"})
            return code == 400 and not app.store.records("PRACTICE"), {"code": code, "response": data}
    s.check("practice_without_operator_rejected", "Empty practice never creates an evaluation or saved chat", no_operator)

    def invalid_decision_rejections():
        with platform(False) as (app, _, req):
            eid = seed_result(app)
            aid = app.store.create_appeal(eid, "c01", "Проверьте")
            bodies = [{"action": "change", "reason": "bad", "new_answer": "na"}, {"action": "change", "reason": "bad", "new_answer": "yes"},
                      {"action": "confirm", "reason": " "}, {"action": "delete", "reason": "bad"}]
            codes = [req(f"/api/appeals/{aid}/decision", b)[0] for b in bodies]
            return codes == [400] * 4 and app.store.appeal(aid)["status"] == "open", {"codes": codes, "status": app.store.appeal(aid)["status"]}
    s.check("invalid_decisions_have_no_side_effects", "Disallowed N/A, unchanged answer, empty reason, unknown action are rejected", invalid_decision_rejections)

    def all_na():
        ck = {"criteria": [{"id": "only", "type": "binary", "weight": 100, "na_allowed": True}]}
        actual = score(ck, {"only": "na"})
        return actual["total"] is None and bool(actual["total_reason"]), actual
    s.check("all_na_no_fabricated_score", "All N/A returns null score with explanation", all_na)

    def live():
        with platform(False) as (app, _, req):
            key = app.store.get_setting("active_checklist")
            app.live.start("QA", "qa-chat", key)
            app.live.update("QA", "qa-chat", json.dumps(payload(app.store.checklist()), ensure_ascii=False)[:-10])
            _, during = req("/api/live/QA/qa-chat")
            app.live.finish("QA", "qa-chat")
            _, after = req("/api/live/QA/qa-chat")
            return during["state"] == "writing" and "c01" in during["criteria"] and after["state"] == "none", {"during": during["state"], "criteria": len(during["criteria"]), "after": after["state"]}
    s.check("live_partial_json_and_cleanup", "Partial JSON publishes completed criteria; finish removes live state", live)

    def voice_local():
        import math
        import struct
        import wave
        from app.voice import analyze
        from cqa.dataset import normalize
        with tempfile.TemporaryDirectory(prefix="cqa-voice-qa-") as tmp:
            wav = Path(tmp) / "fixture.wav"
            with wave.open(str(wav), "wb") as f:
                f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                f.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 180 * i / 16000)) if i % 16000 < 11000 else 0) for i in range(6 * 16000)))
            r = record(); r["channel"] = "voice call"
            rep = analyze(normalize(r), wav)
            return rep["duration"] == 6 and len(rep["peaks"]) == 1200 and all(0 <= p <= 100 for p in rep["peaks"]), {"duration": rep["duration"], "waveform_bins": len(rep["peaks"]), "talk_share": rep["operator"]["talk_share"]}
    s.check("real_local_audio_decode_and_analysis", "Real generated WAV decodes to bounded waveform and voice report", voice_local)

    def voice_cache():
        import wave
        from app.voice import voice_report
        from cqa.dataset import normalize
        with tempfile.TemporaryDirectory(prefix="cqa-voice-cache-qa-") as tmp:
            public = Path(tmp) / "public"; public.mkdir()
            wav = public / "fixture.wav"
            with wave.open(str(wav), "wb") as f:
                f.setparams((1, 2, 16000, 0, "NONE", "not compressed")); f.writeframes(bytes(6 * 16000 * 2))
            a = record("same-cid"); a["channel"] = "voice call"
            first = voice_report(normalize(a), "/fixture.wav", public, Path(tmp) / "cache")
            b = deepcopy(a); b["transcript"][3]["speaker"] = "client"
            second = voice_report(normalize(b), "/fixture.wav", public, Path(tmp) / "cache")
            role = next(x["role"] for x in second["segments"] if x["id"] == "m4")
            return role == "client", {"current_transcript_m4": "client", "report_m4": role, "cached_report_reused": first == second}
    s.check("voice_cache_includes_transcript_roles", "Same ID/timestamps with different speaker roles gets its own voice report", voice_cache)

    def assistance():
        with platform() as (app, model, req):
            eid = seed_result(app)
            coach_code, coach = req(f"/api/evaluations/{eid}/coach", {"criterion_id": "c01"})
            aid = app.store.create_appeal(eid, "c01", "Представление есть")
            c1, opinion = req(f"/api/appeals/{aid}/assist", {})
            before = len(model.calls)
            c2, cached = req(f"/api/appeals/{aid}/assist", {})
            return (coach_code == c1 == c2 == 200 and coach["mode"] == "insert_after" and opinion == cached
                    and len(model.calls) == before and app.store.appeal(aid)["status"] == "open"), {"coach_mode_for_client_target": coach.get("mode"), "opinion_cached": cached == opinion, "SDK_calls": len(model.calls), "appeal_remains_open": app.store.appeal(aid)["status"] == "open"}
    s.check("coach_target_safety_and_cached_nonbinding_opinion", "Coach cannot replace client text; second opinion caches and does not decide appeal", assistance)

    def copilot_paths():
        with platform() as (app, model, req):
            _, start = req("/api/copilot/start", {"dataset_id": "QA", "conversation_id": "qa-chat"})
            messages = start["opening"]
            _, hint = req("/api/copilot/hint", {"messages": messages, "language": "RU", "operator_name": "Алина"})
            _, client = req("/api/copilot/client", {"dataset_id": "QA", "conversation_id": "qa-chat", "messages": messages, "language": "RU"})
            return len(hint["replies"]) == 3 and client["leaves"] and all(c["model"] == "claude-haiku-5-5" for c in model.calls), {"opening_roles": [m["speaker"] for m in messages], "reply_count": len(hint["replies"]), "client_leaves": client["leaves"], "models": [c["model"] for c in model.calls]}
    s.check("copilot_start_hints_client_model_routing", "Copilot API uses Haiku and returns usable opening, hints, simulated client", copilot_paths)

    def instant_checks():
        with platform(False) as (_, _, req):
            checks = []
            for lang, text in (("RU", "Ваш баланс 100 тенге"), ("KZ", "Сіздің шотыңызда ақша бар"), ("EN", "Your balance is 100")):
                _, a = req("/api/copilot/check", {"messages": [], "draft": text, "language": lang})
                _, b = req("/api/copilot/check", {"messages": [{"speaker": "system", "text": "Личность клиента подтверждена."}], "draft": text, "language": lang})
                checks.append({"language": lang, "before": any(h["severity"] == "stop" for h in a["instant"]), "after": any(h["severity"] == "stop" for h in b["instant"])})
            return all(c["before"] and not c["after"] for c in checks), checks
    s.check("instant_disclosure_guard_ru_kz_en", "Account details warn before identity confirmation and stop warning after it", instant_checks)

    def variant_happy():
        with platform() as (app, _, req):
            eid = seed_result(app); original = deepcopy(app.store.record("QA", "qa-chat"))
            new = "Здравствуйте, меня зовут Алина. Проверю информацию. До свидания!"
            code, out = req(f"/api/evaluations/{eid}/variant", {"target_id": "m4", "mode": "replace", "text": new, "votes": 1})
            job = wait_job(app, out["job"]["id"])
            stored = app.store.record("SANDBOX", out["conversation_id"])
            return code == 200 and job["status"] == "done" and stored["transcript"][3]["text"] == new and app.store.record("QA", "qa-chat") == original, {"variant": out["conversation_id"], "job": job["status"], "source_unchanged": app.store.record("QA", "qa-chat") == original}
    s.check("variant_copies_then_evaluates_without_mutating_source", "Sequential variant edit preserves original and evaluates the copy", variant_happy)

    def submission_rules():
        with platform(False) as (app, _, req):
            app.store.save_dataset("CONTROL", "Control QA", [record(f"control-{i}") for i in range(10)], "qa")
            for i in range(10): seed_result(app, "CONTROL", f"control-{i}")
            eid = app.store.latest("CONTROL")["control-0"]["id"]
            aid = app.store.create_appeal(eid, "c04", "Не закрыта суть")
            app.store.decide(aid, "change", "Часть вопроса не закрыта", 10)
            _, data = req("/api/datasets/CONTROL/submission?kind=prod&team_name=QA&download=1")
            first = data["results"][0]
            # A failed retry now falls back to a ready historical result. To test
            # explicit error acknowledgement, this conversation must have none.
            app.store.x("delete from evaluations where dataset_id=? and conversation_id=?", ("CONTROL", "control-1"))
            app.store.add_evaluation("CONTROL", "control-1", app.store.get_setting("active_checklist"), "error", error="API unavailable")
            blocked, _ = req("/api/datasets/CONTROL/submission?kind=prod&team_name=QA&download=1")
            allowed, errors = req("/api/datasets/CONTROL/submission?kind=prod&team_name=QA&download=1&allow_errors=1")
            return (len(data["results"]) == 10 and first["criteria"][3]["value"] == 10 and first["criteria"][3]["rationale"].startswith("Решение")
                    and blocked == 400 and allowed == 200 and errors["results"][1]["score"] is None), {"first_score": first["score"], "without_ack": blocked, "with_ack": allowed}
    s.check("submission_appeals_types_and_explicit_error_ack", "Official export uses final answers and blocks silent errors", submission_rules)

    def submission_history():
        with platform(False) as (app, _, req):
            v1 = app.store.get_setting("active_checklist")
            seed_result(app, key=v1)
            ck = app.store.checklist(v1); ck["name"] += " v2"
            v2 = app.store.save_checklist(ck)
            seed_result(app, key=v2, answers={"c01": "no"})
            app.store.add_evaluation("QA", "qa-chat", v1, "error", error="Failed retry")
            query = "?kind=prod&team_name=QA&checklist=" + urllib.parse.quote(v1)
            code, data = req("/api/datasets/QA/submission" + query + "&download=1")
            _, report = req("/api/datasets/QA/submission" + query)
            _, analytics = req("/api/analytics/QA?checklist=" + urllib.parse.quote(v1))
            return code == 200 and data["results"][0]["score"]["final_score"] == 100 and report["ready"] == 1, {
                "download_code": code, "submission_ready": report["ready"], "submission_errors": report["errors"],
                "submission_notices": report.get("notices"), "historical_total": data["results"][0].get("score"),
                "same_version_analytics_evaluated": analytics["n_evaluated"]}
    s.check("submission_historical_ready_after_other_version_and_failed_retry", "Official submission retains chosen version's ready result after other versions and failed retries", submission_history)

    def submission_review():
        with platform(False) as (app, _, req):
            eid = seed_result(app)
            app.store.x("update evaluations set status=? where id=?", ("needs_review", eid))
            code, data = req("/api/datasets/QA/submission?kind=prod&team_name=QA&download=1")
            _, report = req("/api/datasets/QA/submission?kind=prod&team_name=QA")
            return code == 200 and data["results"][0]["status"] == "ok" and report["warnings"] == 1 and report["rows"][0]["warning"], {
                "download_code": code, "export_status": data["results"][0]["status"], "warnings": report["warnings"],
                "warning": report["rows"][0]["warning"], "count_notices": report.get("notices")}
    s.check("submission_needs_review_exports_with_visible_warning", "Current export contract permits needs_review and wrong set size with explicit report warnings", submission_review)
    return s.results


def serve(port):
    model = LocalModel()
    tmp = tempfile.TemporaryDirectory(prefix="cqa-ui-qa-")
    app = App(Path(tmp.name) / "ui.db", client_factory=lambda: model)
    model.app = app
    app.store.save_dataset("QA", "QA · проверка платформы", [record()], "qa")
    seed_result(app)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    print(f"Isolated QA server at http://127.0.0.1:{port}; model is local, no API calls", flush=True)
    try:
        srv.serve_forever()
    finally:
        app.pool.shutdown(); srv.server_close(); app.store.db.close(); tmp.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    # Do not load .env. Strip inherited provider credentials as an additional guard.
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ["CQA_QUIET"] = "1"
    for key, value in {"CQA_MODEL": "claude-opus-5-5", "CQA_EFFORT": "high", "CQA_VOTES": "3", "CQA_EXTRA_VOTES": "2"}.items():
        os.environ[key] = value
    if args.serve:
        serve(args.port)
    else:
        results = run_suite()
        report = {"passed": sum(r["passed"] for r in results), "failed": sum(not r["passed"] for r in results), "checks": results}
        if args.output:
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "checks"}))
        sys.exit(bool(report["failed"]))
