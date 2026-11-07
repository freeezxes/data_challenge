"""Сервер CQA: JSON API + статика интерфейса. Только стандартная библиотека и пакет cqa.

    python -m app.server            # http://127.0.0.1:8000

Оценка идёт в фоне пулом потоков: интерфейс опрашивает задание и видит прогресс,
каждый диалог сохраняется сразу после ответа модели.
"""
from __future__ import annotations

import csv
import dataclasses
import io
import json
import mimetypes
import os
import re
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cqa.checklist import load_checklist  # noqa: E402
from cqa.dataset import fetch_dataset, normalize  # noqa: E402
from cqa.evaluator import EvalConfig, Evaluator, PRICES  # noqa: E402
from cqa.prompt import build_system_prompt, build_user_message  # noqa: E402
from cqa.gold import load_gold  # noqa: E402
from cqa.metrics import compare  # noqa: E402

from .store import Store, StoreError  # noqa: E402

PUBLIC = ROOT / "public"
RECORDINGS = json.loads((PUBLIC / "recordings.json").read_text(encoding="utf-8")) if (PUBLIC / "recordings.json").exists() else {}
GOLD_PATH = ROOT / "data/gold/dev_gold.json"


def load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


class App:
    """Состояние сервера: хранилище, пул оценки, настройки модели."""

    def __init__(self, db_path, client_factory=None, workers=None):
        self.store = Store(db_path)
        self.config = EvalConfig(model=os.environ.get("CQA_MODEL", "claude-opus-5-5"),
                                 effort=os.environ.get("CQA_EFFORT", "high"),
                                 votes=int(os.environ.get("CQA_VOTES", "3")))
        self.client_factory = client_factory
        self.pool = ThreadPoolExecutor(max_workers=workers or int(os.environ.get("CQA_WORKERS", "3")))
        self.seed()

    def seed(self):
        """Первый запуск: чек-лист организаторов и DEV из репозитория."""
        if not self.store.checklists():
            self.store.import_checklist(load_checklist(ROOT / "config/checklist.default.json"), "Чек-лист организаторов")
        if not self.store.datasets() and (PUBLIC / "dataset.json").exists():
            payload = json.loads((PUBLIC / "dataset.json").read_text(encoding="utf-8"))
            self.store.save_dataset("DEV", "DEV · 65 диалогов", payload["data"]["records"], "public/dataset.json")

    def has_key(self):
        return bool(os.environ.get("ANTHROPIC_API_KEY")) or self.client_factory is not None

    def client(self):
        if self.client_factory:
            return self.client_factory()
        import anthropic
        return anthropic.Anthropic(timeout=180.0, max_retries=4)

    # --- оценка ---
    def start_job(self, ds_id, ids=None, checklist_key=None, only_missing=False, votes=None):
        if not self.has_key():
            raise StoreError("Не задан ANTHROPIC_API_KEY: добавьте его в .env и перезапустите сервер")
        checklist_key = checklist_key or self.store.get_setting("active_checklist")
        checklist = self.store.checklist(checklist_key)
        records = {r["conversation_id"]: r for r in self.store.records(ds_id)}
        ids = ids or list(records)
        unknown = [i for i in ids if i not in records]
        if unknown:
            raise StoreError(f"нет таких диалогов: {', '.join(unknown[:5])}")
        if only_missing:
            latest = self.store.latest(ds_id)
            ids = [i for i in ids if not (i in latest and latest[i]["checklist_key"] == checklist_key
                                          and latest[i]["status"] != "error")]
        if not ids:
            raise StoreError("все выбранные диалоги уже оценены этой версией чек-листа")
        job_id = self.store.create_job(ds_id, checklist_key, len(ids))
        votes = int(votes or self.config.votes)
        if not 1 <= votes <= 5:
            raise StoreError("число прогонов — от 1 до 5")
        evaluator = Evaluator(checklist, client=self.client(), config=dataclasses.replace(self.config, votes=votes))
        remaining = {"n": len(ids)}
        lock = threading.Lock()

        def run(conv_id):
            ok, cost = False, None
            try:
                result = evaluator.evaluate(normalize(records[conv_id]))
                result["calls"] = [_trim_call(c) for c in result.get("calls", [])]  # для «Как получена оценка»
                cost = result.get("cost_usd")
                self.store.add_evaluation(ds_id, conv_id, checklist_key, result["status"], result, job_id=job_id)
                ok = True
            except Exception as exc:  # ошибка одного диалога не останавливает остальные
                self.store.add_evaluation(ds_id, conv_id, checklist_key, "error", error=_short(exc), job_id=job_id)
                traceback.print_exc()
            finally:
                self.store.job_progress(job_id, ok, cost)
                with lock:
                    remaining["n"] -= 1
                    if remaining["n"] == 0:
                        job = self.store.job(job_id)
                        self.store.finish_job(job_id, "done" if not job["failed"] else "done_with_errors")

        def launch():
            # Одновременных вызовов несколько — сначала один раз пишем кэш промпта, потом все читают его.
            if votes * min(len(ids), self.pool._max_workers) > 1:
                try:
                    self.store.job_progress(job_id, None, evaluator.warm_cache())
                except Exception:
                    traceback.print_exc()
            for conv_id in ids:
                self.pool.submit(run, conv_id)

        threading.Thread(target=launch, daemon=True).start()
        return self.store.job(job_id)

    # --- представления для интерфейса ---
    def state(self):
        key = self.store.get_setting("active_checklist")
        price = PRICES.get(self.config.model)
        return {
            "active_checklist": key,
            "active_checklist_name": self.store.checklist(key).get("name"),
            "model": self.config.model, "effort": self.config.effort, "votes": self.config.votes,
            "price_per_mtok": price,
            "has_key": self.has_key(), "has_api_url": bool(os.environ.get("CQA_API_URL")),
            "datasets": self.store.datasets(),
            "open_appeals": len(self.store.appeals("open")),
            "jobs": self.store.jobs(active_only=True),
        }

    def queue(self, ds_id):
        latest = self.store.latest(ds_id)
        rows = []
        for r in self.store.records(ds_id):
            cid = r["conversation_id"]
            transcript = r["transcript"] if isinstance(r["transcript"], list) else json.loads(r["transcript"])
            said = [m["text"] for m in transcript if m["speaker"] == "client"]
            # в звонках первые «реплики» клиента — нажатия в меню («2», «0»): берём первую осмысленную
            first_client = next((t for t in said if len(t.split()) >= 3), said[0] if said else "")
            ev = latest.get(cid)
            row = {"conversation_id": cid, "channel": r.get("channel"), "language": r.get("language"),
                   "topic": first_client[:90], "messages": len(transcript), "evaluation": None}
            if ev:
                fin = ev.get("final")
                row["evaluation"] = {
                    "id": ev["id"], "status": ev["status"], "checklist_key": ev["checklist_key"], "error": ev["error"],
                    "total": fin["score"]["total"] if fin else None,
                    "original_total": ev["result"]["score"]["total"] if ev["result"] else None,
                    "critical": fin["score"]["critical"] if fin else False,
                    "violations": sorted({v for d in (ev["result"] or {}).get("criteria", {}).values() for v in d.get("violations", [])}),
                    "open_appeals": sum(a["status"] == "open" for a in ev["appeals"]),
                    "uncertain": sum(1 for d in (ev["result"] or {}).get("criteria", {}).values() if (d.get("confidence") or 1) < 1),
                    "votes": (ev["result"] or {}).get("votes"),
                    "decided_appeals": sum(a["status"] == "decided" for a in ev["appeals"]),
                }
            rows.append(row)
        return rows

    def conversation(self, ds_id, conv_id):
        record = self.store.record(ds_id, conv_id)
        conv = normalize(record)
        audio = RECORDINGS.get(conv_id) or record.get("audio_url")
        evs = self.store.evaluations(ds_id, conv_id)
        checklists = {e["checklist_key"]: self.store.checklist(e["checklist_key"]) for e in evs}
        return {"conversation": conv, "audio": audio, "evaluations": evs, "checklists": checklists}

    def analytics(self, ds_id, checklist_key=None):
        checklist_key = checklist_key or self.store.get_setting("active_checklist")
        checklist = self.store.checklist(checklist_key)
        latest = {k: v for k, v in self.store.latest(ds_id).items()
                  if v["checklist_key"] == checklist_key and v["result"]}
        records = {r["conversation_id"]: r for r in self.store.records(ds_id)}
        out = {"checklist_key": checklist_key, "n_total": len(records), "n_evaluated": len(latest),
               "criteria": [], "by_language": {}, "by_channel": {}, "critical": 0, "cost_usd": 0.0,
               "appeals": {"open": 0, "confirmed": 0, "changed": 0}, "needs_review": 0, "gold": None}
        if not latest:
            return out
        totals = []
        for cid, ev in latest.items():
            fin = ev["final"]
            t = fin["score"]["total"]
            if t is not None:
                totals.append(t)
                for field, key in (("language", "by_language"), ("channel", "by_channel")):
                    out[key].setdefault(records[cid].get(field) or "—", []).append(t)
            out["critical"] += int(fin["score"]["critical"])
            out["cost_usd"] += ev["result"].get("cost_usd") or 0
            out["needs_review"] += int(ev["status"] == "needs_review")
            for a in ev["appeals"]:
                out["appeals"]["open" if a["status"] == "open" else ("changed" if a["action"] == "change" else "confirmed")] += 1
        avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
        out["avg_total"] = avg(totals)
        out["by_language"] = {k: {"avg": avg(v), "n": len(v)} for k, v in sorted(out["by_language"].items())}
        out["by_channel"] = {k: {"avg": avg(v), "n": len(v)} for k, v in sorted(out["by_channel"].items())}
        out["cost_per_dialog"] = round(out["cost_usd"] / len(latest), 4)
        for c in checklist["criteria"]:
            answers = [ev["final"]["answers"][c["id"]] for ev in latest.values()]
            applicable = [a for a in answers if a != "na"]
            if c["type"] == "scale":
                top = max(s["value"] for s in c["scale"])
                rate = sum(a / top for a in applicable) / len(applicable) if applicable else None
            elif c["type"] == "critical":
                rate = sum(a == "yes" for a in applicable) / len(applicable) if applicable else None
            else:
                rate = sum(a == "yes" for a in applicable) / len(applicable) if applicable else None
            out["criteria"].append({"id": c["id"], "title": c["title"], "type": c["type"], "weight": c["weight"],
                                    "rate": round(rate, 3) if rate is not None else None,
                                    "na": len(answers) - len(applicable), "n": len(answers)})
        out["gold"] = self.gold_compare(checklist, latest)
        return out

    def gold_compare(self, checklist, latest):
        """Сверка с рабочей разметкой команды — только для того же набора критериев (DEV)."""
        if not GOLD_PATH.exists():
            return None
        gold = load_gold(GOLD_PATH)
        some = next(iter(gold["items"].values()))["answers"]
        if {c["id"] for c in checklist["criteria"]} != set(some):
            return None
        preds = {k: v["result"]["answers"] for k, v in latest.items() if k in gold["items"]}
        if not preds:
            return None
        rep = compare(checklist, gold, preds)
        return {"n": rep["n_conversations"], "macro_accuracy": rep["macro_accuracy"],
                "all_exact": rep["all_criteria_exact"], "total_mae": rep["total_mae"],
                "criteria": {k: v["accuracy"] for k, v in rep["criteria"].items()},
                "note": "Рабочая разметка команды, не официальный эталон организаторов."}


def _trim_call(call: dict) -> dict:
    """Сырой ответ модели для журнала оценки: текст JSON, токены, причина остановки."""
    text = "".join(b.get("text", "") for b in call.get("content", []))
    return {"model": call.get("model"), "usage": call.get("usage"), "stop_reason": call.get("stop_reason"),
            "text": text[:60000]}


class FileResponse:
    def __init__(self, name, ctype, data: bytes):
        self.name, self.ctype, self.data = name, ctype, data


def _short(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    text = re.sub(r"sk-ant-[\w-]+", "sk-ant-…", text)
    return text[:400]


# Явные типы: на Windows mimetypes берёт их из реестра и может отдать .js как text/plain —
# тогда браузер не загрузит модули интерфейса.
MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
        ".woff2": "font/woff2", ".ttf": "font/ttf", ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".txt": "text/plain", ".pdf": "application/pdf"}

ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


@route("GET", r"/api/state")
def _state(app, m, q, body):
    return app.state()


@route("GET", r"/api/checklists")
def _checklists(app, m, q, body):
    return app.store.checklists()


@route("GET", r"/api/checklists/(?P<key>[^/]+)")
def _checklist(app, m, q, body):
    return app.store.checklist(m["key"])


@route("POST", r"/api/checklists")
def _save_checklist(app, m, q, body):
    key = app.store.save_checklist(body["body"], body.get("note", ""), body.get("activate", True))
    return {"key": key, "checklist": app.store.checklist(key)}


@route("POST", r"/api/checklists/(?P<key>[^/]+)/activate")
def _activate(app, m, q, body):
    app.store.activate(m["key"])
    return {"active_checklist": m["key"]}


@route("GET", r"/api/datasets/(?P<ds>[^/]+)/conversations")
def _queue(app, m, q, body):
    return app.queue(m["ds"])


@route("POST", r"/api/datasets")
def _upload(app, m, q, body):
    payload = body.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise StoreError(f"файл не похож на JSON: {exc.msg}, строка {exc.lineno}") from exc
    if isinstance(payload, dict) and "data" in payload:
        if payload.get("isSuccess") is False:
            raise StoreError(f"в файле ответ API с ошибкой: {payload.get('error')}")
        payload = payload["data"]
    if isinstance(payload, dict) and "records" in payload:
        records = payload["records"]
    elif isinstance(payload, dict) and "conversation_id" in payload:
        records = [payload]  # один транскрипт
    elif isinstance(payload, list):
        records = payload
    else:
        raise StoreError("ожидается ответ API, объект data, массив records или одна запись с conversation_id и transcript")
    if body.get("append_to"):
        existing = {d["id"]: d for d in app.store.datasets()}
        if body["append_to"] not in existing:
            raise StoreError(f"набор {body['append_to']} не найден")
        ds_id, name = body["append_to"], existing[body["append_to"]]["name"]
    else:
        ds_id = _slug(body.get("id") or (payload.get("dataset_group_id") if isinstance(payload, dict) else None)
                      or body.get("name") or "upload")
        name = body.get("name") or ds_id
    n = app.store.save_dataset(ds_id, name, records, "upload")
    return {"id": ds_id, "count": n, "ids": [r["conversation_id"] for r in records]}


@route("POST", r"/api/datasets/fetch")
def _fetch(app, m, q, body):
    base = os.environ.get("CQA_API_URL")
    if not base:
        raise StoreError("Не задан CQA_API_URL — адрес GET из гайда организаторов (в .env)")
    group = (body.get("group_id") or "").strip()
    if not group:
        raise StoreError("укажите group_id")
    try:
        payload = fetch_dataset(base, group)
    except Exception as exc:
        raise StoreError(f"API организаторов: {_short(exc)}") from exc
    data = payload["data"]
    n = app.store.save_dataset(_slug(group), f"{group} · {data.get('total_records', len(data['records']))} диалогов",
                               data["records"], "organizer API")
    return {"id": _slug(group), "count": n}


@route("GET", r"/api/conversations/(?P<ds>[^/]+)/(?P<cid>[^/]+)")
def _conversation(app, m, q, body):
    return app.conversation(m["ds"], m["cid"])


@route("POST", r"/api/jobs")
def _start(app, m, q, body):
    return app.start_job(body["dataset_id"], body.get("conversation_ids"), body.get("checklist_key"),
                         bool(body.get("only_missing")), body.get("votes"))


@route("GET", r"/api/jobs")
def _jobs(app, m, q, body):
    return app.store.jobs(active_only=q.get("active") == "1")


@route("GET", r"/api/jobs/(?P<id>\d+)")
def _job(app, m, q, body):
    return app.store.job(int(m["id"]))


@route("GET", r"/api/appeals")
def _appeals(app, m, q, body):
    rows = app.store.appeals(q.get("status"))
    for r in rows:
        ev = app.store.evaluation(r["evaluation_id"])
        crit = next(c for c in app.store.checklist(ev["checklist_key"])["criteria"] if c["id"] == r["criterion_id"])
        r["criterion_title"] = crit["title"]
        r["original_answer"] = ev["result"]["answers"][r["criterion_id"]]
        r["original_total"] = ev["result"]["score"]["total"]
        r["final_total"] = ev["final"]["score"]["total"]
    return rows


@route("POST", r"/api/appeals")
def _appeal(app, m, q, body):
    ap_id = app.store.create_appeal(int(body["evaluation_id"]), body["criterion_id"], body.get("comment", ""))
    return app.store.appeal(ap_id)


@route("POST", r"/api/appeals/(?P<id>\d+)/decision")
def _decide(app, m, q, body):
    return app.store.decide(int(m["id"]), body.get("action"), body.get("reason", ""), body.get("new_answer"))


@route("POST", r"/api/evaluations/(?P<id>\d+)/preview")
def _preview(app, m, q, body):
    return app.store.preview(int(m["id"]), body["criterion_id"], body["new_answer"])


@route("GET", r"/api/analytics/(?P<ds>[^/]+)")
def _analytics(app, m, q, body):
    return app.analytics(m["ds"], q.get("checklist"))


@route("GET", r"/api/checklists/(?P<key>[^/]+)/prompt")
def _prompt(app, m, q, body):
    return {"system_prompt": build_system_prompt(app.store.checklist(m["key"]))}


@route("GET", r"/api/evaluations/(?P<id>\d+)/trace")
def _trace(app, m, q, body):
    """Как получена оценка: что ушло в модель, что она ответила, что посчитал код."""
    ev = app.store.evaluation(int(m["id"]))
    conv = normalize(app.store.record(ev["dataset_id"], ev["conversation_id"]))
    res = ev["result"] or {}
    return {"evaluation_id": ev["id"], "checklist_key": ev["checklist_key"], "source": ev["source"],
            "created_at": ev["created_at"], "status": ev["status"], "error": ev["error"],
            "model": res.get("model"), "served_models": res.get("served_models"), "effort": res.get("effort"),
            "votes": res.get("votes"), "prompt_hash": res.get("prompt_hash"), "latency_s": res.get("latency_s"),
            "cost_usd": res.get("cost_usd"), "usage": res.get("usage"), "issues": res.get("issues"),
            "facts": res.get("facts"), "calls": res.get("calls") or [],
            "per_criterion_votes": {k: v.get("votes") for k, v in res.get("criteria", {}).items()},
            "derived": {k: {"facts": v.get("facts"), "model_answer": v.get("model_answer"), "answer": v.get("answer")}
                        for k, v in res.get("criteria", {}).items() if "facts" in v},
            "system_prompt": build_system_prompt(app.store.checklist(ev["checklist_key"])),
            "user_message": build_user_message(conv)}


@route("GET", r"/api/datasets/(?P<ds>[^/]+)/export")
def _export(app, m, q, body):
    """Результаты набора для сверки с эталоном: финальные ответы (после апелляций) и исходные."""
    key = q.get("checklist") or app.store.get_setting("active_checklist")
    checklist = app.store.checklist(key)
    latest = app.store.latest(m["ds"])
    if q.get("checklist"):
        latest = {k: v for k, v in latest.items() if v["checklist_key"] == key}
    ids = [c["id"] for c in checklist["criteria"]]
    rows = []
    for r in app.store.records(m["ds"]):
        ev = latest.get(r["conversation_id"])
        row = {"conversation_id": r["conversation_id"], "channel": r.get("channel"), "language": r.get("language")}
        if ev and ev["result"]:
            fin = ev["final"]
            row.update({"checklist": ev["checklist_key"], "status": ev["status"], "total": fin["score"]["total"],
                        "total_model": ev["result"]["score"]["total"], "critical": fin["score"]["critical"],
                        "answers": fin["answers"], "answers_model": ev["result"]["answers"],
                        "cost_usd": ev["result"].get("cost_usd"), "evaluated_at": ev["created_at"]})
        else:
            row.update({"status": ev["status"] if ev else "not_evaluated", "answers": {}})
        rows.append(row)
    if q.get("format") == "json":
        return FileResponse(f"cqa-{m['ds']}.json", "application/json",
                            json.dumps({"dataset": m["ds"], "checklist": key, "criteria": ids, "results": rows},
                                       ensure_ascii=False, indent=1).encode())
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["conversation_id", "channel", "language", "status", *ids, "total", "critical", "total_model",
                *[f"{i}_model" for i in ids], "checklist", "cost_usd"])
    for r in rows:
        a, am = r.get("answers", {}), r.get("answers_model", {})
        w.writerow([r["conversation_id"], r["channel"], r["language"], r["status"], *[a.get(i, "") for i in ids],
                    r.get("total", ""), r.get("critical", ""), r.get("total_model", ""), *[am.get(i, "") for i in ids],
                    r.get("checklist", ""), r.get("cost_usd", "")])
    return FileResponse(f"cqa-{m['ds']}.csv", "text/csv; charset=utf-8", ("\ufeff" + out.getvalue()).encode())


def _slug(s: str) -> str:
    s = re.sub(r"[^\w.-]+", "-", str(s).strip(), flags=re.UNICODE).strip("-")
    return s[:40] or "dataset"


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CQA/1.0"

        def log_message(self, fmt, *args):
            if os.environ.get("CQA_QUIET") != "1":
                sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

        def _json(self, status, data):
            raw = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _api(self, method):
            url = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            body = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                if n > 50_000_000:
                    return self._json(413, {"error": "файл больше 50 МБ"})
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._json(400, {"error": "тело запроса — не JSON"})
            for meth, rx, fn in ROUTES:
                m = rx.match(url.path)
                if m and meth == method:
                    try:
                        out = fn(app, {k: unquote(v) for k, v in m.groupdict().items()}, q, body)
                        if isinstance(out, FileResponse):
                            self.send_response(200)
                            self.send_header("Content-Type", out.ctype)
                            self.send_header("Content-Length", str(len(out.data)))
                            self.send_header("Content-Disposition", f'attachment; filename="{out.name}"')
                            self.end_headers()
                            return self.wfile.write(out.data)
                        return self._json(200, out)
                    except (StoreError, KeyError, ValueError) as exc:
                        msg = str(exc) if not isinstance(exc, KeyError) else f"не хватает поля {exc}"
                        return self._json(400, {"error": msg})
                    except Exception as exc:
                        traceback.print_exc()
                        return self._json(500, {"error": _short(exc)})
            return self._json(404, {"error": "нет такого адреса API"})

        def do_GET(self):
            if self.path.startswith("/api/"):
                return self._api("GET")
            return self._static()

        def do_HEAD(self):
            return self._static(head=True)

        def do_POST(self):
            if self.path.startswith("/api/"):
                return self._api("POST")
            self.send_error(405)

        def _static(self, head=False):
            path = urlsplit(self.path).path
            path = "/index.html" if path == "/" else path
            file = (PUBLIC / path.lstrip("/")).resolve()
            if PUBLIC.resolve() not in file.parents or not file.is_file():
                return self.send_error(404, "Файл не найден")
            data = file.read_bytes()
            ctype = MIME.get(file.suffix.lower()) or mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            rng = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
            if rng and (rng[1] or rng[2]):  # перемотка записи звонка в браузере
                start = int(rng[1]) if rng[1] else len(data) - int(rng[2])
                end = min(int(rng[2]) if rng[1] and rng[2] else len(data) - 1, len(data) - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(data)}")
                    return self.end_headers()
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                data = data[start:end + 1]
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not head:
                self.wfile.write(data)

    return Handler


def main():
    load_env()
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    app = App(os.environ.get("CQA_DB", ROOT / "data/cqa.db"))
    srv = ThreadingHTTPServer((host, port), make_handler(app))
    key = "есть" if app.has_key() else "НЕТ — оценка недоступна, добавьте ANTHROPIC_API_KEY в .env"
    print(f"CQA: http://{host}:{port}  ·  модель {app.config.model}  ·  ключ API: {key}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
