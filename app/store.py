"""Хранилище продукта: SQLite из стандартной библиотеки, JSON в колонках.

Сущности:
- чек-листы — неизменяемые версии (`<id>@v<n>`), одна из них активная;
- наборы диалогов и их записи в исходном формате API организаторов;
- оценки — результат оценщика целиком, со снимком версии чек-листа;
- апелляции — спор по одному критерию, решение проверяющего и пересчитанный итог.

Исходная оценка никогда не переписывается: финальный результат считается поверх неё
по решённым апелляциям, поэтому в карточке всегда видны оба.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from cqa.checklist import criterion_map, normalize_answer, validate_checklist
from cqa.scoring import score

SCHEMA = """
create table if not exists settings(k text primary key, v text);
create table if not exists checklists(
  key text primary key, cid text not null, version integer not null, name text,
  body text not null, note text, created_at text not null);
create table if not exists datasets(
  id text primary key, name text not null, source text, created_at text not null);
create table if not exists conversations(
  dataset_id text not null, conversation_id text not null, position integer, record text not null,
  primary key(dataset_id, conversation_id));
create table if not exists jobs(
  id integer primary key autoincrement, dataset_id text, checklist_key text, total integer, done integer default 0,
  failed integer default 0, status text, error text, cost_usd real default 0, created_at text, finished_at text);
create table if not exists evaluations(
  id integer primary key autoincrement, dataset_id text not null, conversation_id text not null,
  checklist_key text not null, job_id integer, status text not null, result text, error text,
  source text, created_at text not null);
create index if not exists ev_conv on evaluations(dataset_id, conversation_id, id);
create table if not exists job_items(
  dataset_id text, conversation_id text, checklist_key text, job_id integer,
  primary key(dataset_id, conversation_id, checklist_key));
create table if not exists appeals(
  id integer primary key autoincrement, evaluation_id integer not null, criterion_id text not null,
  comment text not null, status text not null default 'open', action text, new_answer text, reason text,
  created_at text not null, decided_at text);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StoreError(ValueError):
    """Ошибка входных данных: сервер отвечает 400 с этим текстом."""


def checklist_problems(body: dict) -> list[str]:
    """Структурные ошибки + правило из чек-листа: веса применимых критериев дают 100."""
    errors = validate_checklist(body)
    if errors:
        return errors
    if not body.get("name"):
        errors.append("у чек-листа нет названия")
    for c in body.get("criteria", []):
        if not c.get("title"):
            errors.append(f"{c.get('id')}: нет названия")
        if c.get("type") == "critical" and not c.get("violations"):
            errors.append(f"{c.get('id')}: у грубой ошибки нет списка нарушений")
        if c.get("type") in ("binary", "scale") and not (c.get("yes_when") or c.get("scale") or c.get("guidance")):
            errors.append(f"{c.get('id')}: опишите, когда ставить оценку")
    total = sum(c.get("weight") or 0 for c in body.get("criteria", []) if c.get("type") != "critical")
    if abs(total - 100) > 1e-9:
        errors.append(f"сумма весов {total:g}, должна быть 100")
    return errors


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("pragma table_info(appeals)")}
        if "assist" not in cols:  # второе мнение модели по апелляции (добавлено позже)
            self.db.execute("alter table appeals add column assist text")
            self.db.commit()

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid

    # --- настройки ---
    def get_setting(self, k, default=None):
        rows = self.q("select v from settings where k=?", (k,))
        return rows[0]["v"] if rows else default

    def set_setting(self, k, v):
        self.x("insert into settings(k,v) values(?,?) on conflict(k) do update set v=excluded.v", (k, v))

    # --- чек-листы ---
    def save_checklist(self, body: dict, note: str = "", activate: bool = True) -> str:
        if not isinstance(body, dict):
            raise StoreError("чек-лист должен быть объектом")
        body = json.loads(json.dumps(body))
        body["id"] = body.get("id") or "checklist"
        body.setdefault("rounding", 2)
        problems = checklist_problems(body)
        if problems:
            raise StoreError("; ".join(problems))
        with self.lock:
            last = self.q("select max(version) v from checklists where cid=?", (body["id"],))[0]["v"] or 0
            body["version"] = last + 1
            key = f"{body['id']}@v{body['version']}"
            self.x("insert into checklists(key,cid,version,name,body,note,created_at) values(?,?,?,?,?,?,?)",
                   (key, body["id"], body["version"], body.get("name"), json.dumps(body, ensure_ascii=False), note, now()))
            if activate or not self.get_setting("active_checklist"):
                self.set_setting("active_checklist", key)
        return key

    def import_checklist(self, body: dict, note: str) -> str:
        """Первая загрузка: сохраняем как есть, сохраняя номер версии из файла."""
        key = f"{body['id']}@v{body['version']}"
        if not self.q("select 1 from checklists where key=?", (key,)):
            self.x("insert into checklists(key,cid,version,name,body,note,created_at) values(?,?,?,?,?,?,?)",
                   (key, body["id"], body["version"], body.get("name"), json.dumps(body, ensure_ascii=False), note, now()))
        if not self.get_setting("active_checklist"):
            self.set_setting("active_checklist", key)
        return key

    def checklist(self, key: str | None = None) -> dict:
        key = key or self.get_setting("active_checklist")
        rows = self.q("select body from checklists where key=?", (key,))
        if not rows:
            raise StoreError(f"чек-лист {key} не найден")
        return json.loads(rows[0]["body"])

    def checklists(self):
        active = self.get_setting("active_checklist")
        rows = self.q("select key,cid,version,name,note,created_at from checklists order by cid, version desc")
        for r in rows:
            r["active"] = r["key"] == active
            r["evaluations"] = self.q("select count(*) n from evaluations where checklist_key=?", (r["key"],))[0]["n"]
        return rows

    def activate(self, key: str):
        self.checklist(key)
        self.set_setting("active_checklist", key)

    # --- наборы ---
    def save_dataset(self, ds_id: str, name: str, records: list[dict], source: str) -> int:
        if not records:
            raise StoreError("в наборе нет записей")
        for r in records:
            if not r.get("conversation_id") or not isinstance(r.get("transcript"), (list, str)):
                raise StoreError("каждая запись должна содержать conversation_id и transcript")
        with self.lock:
            self.x("insert into datasets(id,name,source,created_at) values(?,?,?,?) on conflict(id) do update set name=excluded.name, source=excluded.source",
                   (ds_id, name, source, now()))
            start = (self.q("select max(position) p from conversations where dataset_id=?", (ds_id,))[0]["p"] or -1) + 1
            for i, r in enumerate(records):
                # новый диалог — в конец набора; уже известный — обновляем текст, место в очереди сохраняем
                self.x("insert into conversations(dataset_id,conversation_id,position,record) values(?,?,?,?) "
                       "on conflict(dataset_id,conversation_id) do update set record=excluded.record",
                       (ds_id, r["conversation_id"], start + i, json.dumps(r, ensure_ascii=False)))
        return len(records)

    def datasets(self):
        rows = self.q("select * from datasets order by created_at")
        for d in rows:
            d["count"] = self.q("select count(*) n from conversations where dataset_id=?", (d["id"],))[0]["n"]
            d["evaluated"] = self.q("select count(*) n from evaluations where id in "
                                      "(select max(id) from evaluations where dataset_id=? group by conversation_id) "
                                      "and status!='error'", (d["id"],))[0]["n"]
        return rows

    def records(self, ds_id: str) -> list[dict]:
        return [json.loads(r["record"]) for r in
                self.q("select record from conversations where dataset_id=? order by position", (ds_id,))]

    def discard_new_conversation(self, ds_id, conv_id):
        """Undo an unsuccessful save-and-evaluate of a newly generated, unique ID."""
        with self.lock, self.db:
            self.db.execute("delete from conversations where dataset_id=? and conversation_id=? "
                            "and not exists (select 1 from evaluations where dataset_id=? and conversation_id=?) "
                            "and not exists (select 1 from job_items where dataset_id=? and conversation_id=?)",
                            (ds_id, conv_id, ds_id, conv_id, ds_id, conv_id))
            self.db.execute("delete from datasets where id=? and not exists "
                            "(select 1 from conversations where dataset_id=?)", (ds_id, ds_id))

    def evaluation_cost(self, ds_id, checklist_key):
        """All attempts plus warmup, without counting job-linked evaluations twice."""
        jobs = self.q("select id,cost_usd from jobs where dataset_id=? and checklist_key=?", (ds_id, checklist_key))
        ids = {j["id"] for j in jobs}
        total = sum(j["cost_usd"] or 0 for j in jobs)
        for ev in self.q("select job_id,result from evaluations where dataset_id=? and checklist_key=?",
                         (ds_id, checklist_key)):
            if ev["job_id"] not in ids and ev["result"]:
                total += json.loads(ev["result"]).get("cost_usd") or 0
        return round(total, 6)

    def record(self, ds_id: str, conv_id: str) -> dict:
        rows = self.q("select record from conversations where dataset_id=? and conversation_id=?", (ds_id, conv_id))
        if not rows:
            raise StoreError(f"диалог {conv_id} не найден в наборе {ds_id}")
        return json.loads(rows[0]["record"])

    # --- задания и оценки ---
    def recover_jobs(self):
        """Single server owns a database; work left by its previous process cannot continue."""
        with self.lock, self.db:
            self.db.execute("update jobs set status='interrupted', error=?, finished_at=? where status='running'",
                            ("Оценка прервана перезапуском сервера. Запустите оставшиеся диалоги заново.", now()))
            self.db.execute("delete from job_items")

    def create_job(self, ds_id, checklist_key, total, ids=()):
        with self.lock, self.db:
            self.db.execute("begin immediate")
            if any(self.db.execute("select 1 from job_items where dataset_id=? and conversation_id=? and checklist_key=?",
                                   (ds_id, cid, checklist_key)).fetchone() for cid in ids):
                raise StoreError("выбранный диалог уже оценивается этой версией чек-листа")
            job_id = self.db.execute("insert into jobs(dataset_id,checklist_key,total,status,created_at) values(?,?,?,?,?)",
                                     (ds_id, checklist_key, total, "running", now())).lastrowid
            self.db.executemany("insert into job_items values(?,?,?,?)", [(ds_id, cid, checklist_key, job_id) for cid in ids])
            return job_id

    def job(self, job_id):
        rows = self.q("select * from jobs where id=?", (job_id,))
        return rows[0] if rows else None

    def jobs(self, active_only=False):
        sql = "select * from jobs where status='running'" if active_only else (
            "select * from jobs where status='running' or id in (select id from jobs order by id desc limit 20)")
        sql += " order by id desc"
        return self.q(sql)

    def job_progress(self, job_id, ok: bool | None, cost: float | None):
        """ok=None — только стоимость (подготовка кэша), без счётчика диалогов."""
        if ok is None:
            return self.x("update jobs set cost_usd=cost_usd+? where id=?", (cost or 0, job_id))
        col = "done" if ok else "failed"
        self.x(f"update jobs set {col}={col}+1, cost_usd=cost_usd+? where id=?", (cost or 0, job_id))

    def finish_job(self, job_id, status="done", error=None):
        with self.lock, self.db:
            self.db.execute("update jobs set status=?, error=?, finished_at=? where id=?", (status, error, now(), job_id))
            self.db.execute("delete from job_items where job_id=?", (job_id,))

    def add_evaluation(self, ds_id, conv_id, checklist_key, status, result=None, error=None, job_id=None, source="live"):
        return self.x("insert into evaluations(dataset_id,conversation_id,checklist_key,job_id,status,result,error,source,created_at) "
                      "values(?,?,?,?,?,?,?,?,?)",
                      (ds_id, conv_id, checklist_key, job_id, status,
                       json.dumps(result, ensure_ascii=False) if result is not None else None, error, source, now()))

    def evaluation(self, ev_id) -> dict:
        rows = self.q("select * from evaluations where id=?", (ev_id,))
        if not rows:
            raise StoreError(f"оценка {ev_id} не найдена")
        return self._expand(rows[0])

    def _expand(self, row: dict) -> dict:
        row = dict(row)
        row["result"] = json.loads(row["result"]) if row.get("result") else None
        row["appeals"] = self.q("select * from appeals where evaluation_id=? order by id", (row["id"],))
        if row["result"] and row["status"] != "error":
            row["final"] = self.final_result(row)
        return row

    def evaluations(self, ds_id, conv_id):
        rows = self.q("select * from evaluations where dataset_id=? and conversation_id=? order by id desc", (ds_id, conv_id))
        return [self._expand(r) for r in rows]

    def latest(self, ds_id, checklist_key=None) -> dict[str, dict]:
        """Последняя оценка по каждому диалогу набора (кратко, для очереди)."""
        rows = self.q("select e.* from evaluations e join (select conversation_id, max(id) mid from evaluations "
                      "where dataset_id=?" + (" and checklist_key=?" if checklist_key else "") +
                      " group by conversation_id) m on e.id=m.mid", (ds_id, checklist_key) if checklist_key else (ds_id,))
        return {r["conversation_id"]: self._expand(r) for r in rows}

    def latest_ready(self, ds_id, checklist_key) -> dict[str, dict]:
        """Последняя готовая оценка (ok или «Проверить») каждого диалога по конкретной версии чек-листа.

        Для выгрузки: оценка по другой версии или неудачная переоценка не закрывают собой готовый результат.
        """
        rows = self.q("select e.* from evaluations e join (select conversation_id, max(id) mid from evaluations "
                      "where dataset_id=? and checklist_key=? and status in ('ok','needs_review') and result is not null "
                      "group by conversation_id) m on e.id=m.mid", (ds_id, checklist_key))
        return {r["conversation_id"]: self._expand(r) for r in rows}

    # --- апелляции ---
    def final_result(self, ev: dict) -> dict:
        """Ответы после решений проверяющего и пересчитанный итог по снимку чек-листа."""
        checklist = self.checklist(ev["checklist_key"])
        answers = dict(ev["result"]["answers"])
        changed = {}
        for a in ev["appeals"]:
            if a["status"] == "decided" and a["action"] == "change":
                c = criterion_map(checklist)[a["criterion_id"]]
                answers[a["criterion_id"]] = normalize_answer(c, a["new_answer"])
                changed[a["criterion_id"]] = a["id"]
        return {"answers": answers, "score": score(checklist, answers), "changed": changed}

    def create_appeal(self, ev_id: int, criterion_id: str, comment: str) -> int:
        ev = self.evaluation(ev_id)
        if not ev.get("final"):
            raise StoreError("оспорить можно только готовую оценку")
        if criterion_id not in ev["result"]["answers"]:
            raise StoreError(f"в оценке нет критерия {criterion_id}")
        if not isinstance(comment, str) or not comment.strip():
            raise StoreError("добавьте комментарий: что не так с оценкой")
        if any(a["criterion_id"] == criterion_id and a["status"] == "open" for a in ev["appeals"]):
            raise StoreError("по этому критерию уже есть апелляция, которая ждёт решения")
        with self.lock, self.db:
            cur = self.db.execute("insert into appeals(evaluation_id,criterion_id,comment,status,created_at) "
                                  "select ?,?,?,?,? where not exists (select 1 from appeals "
                                  "where evaluation_id=? and criterion_id=? and status='open')",
                                  (ev_id, criterion_id, comment.strip(), "open", now(), ev_id, criterion_id))
            if cur.rowcount != 1:
                raise StoreError("по этому критерию уже есть открытая апелляция")
            return cur.lastrowid

    def appeal(self, ap_id):
        rows = self.q("select * from appeals where id=?", (ap_id,))
        if not rows:
            raise StoreError(f"апелляция {ap_id} не найдена")
        return rows[0]

    def appeals(self, status=None):
        rows = self.q("select a.*, e.dataset_id, e.conversation_id, e.checklist_key from appeals a "
                      "join evaluations e on e.id=a.evaluation_id"
                      + (" where a.status=?" if status else "") + " order by a.status='decided', a.id desc",
                      (status,) if status else ())
        return rows

    def decide(self, ap_id: int, action: str, reason: str, new_answer=None) -> dict:
        ap = self.appeal(ap_id)
        if ap["status"] != "open":
            raise StoreError("решение по апелляции уже принято")
        if action not in ("confirm", "change"):
            raise StoreError("решение — confirm или change")
        if not isinstance(reason, str) or not reason.strip():
            raise StoreError("укажите причину решения")
        ev = self.evaluation(ap["evaluation_id"])
        if action == "change":
            c = criterion_map(self.checklist(ev["checklist_key"]))[ap["criterion_id"]]
            try:
                new_answer = normalize_answer(c, new_answer)
            except (ValueError, TypeError) as exc:
                raise StoreError(str(exc)) from exc
            if str(new_answer) == str(ev["final"]["answers"][ap["criterion_id"]]):
                raise StoreError("новая оценка совпадает с текущей — выберите «Подтвердить»")
        else:
            new_answer = None
        with self.lock, self.db:
            cur = self.db.execute("update appeals set status='decided', action=?, new_answer=?, reason=?, decided_at=? "
                                  "where id=? and status='open'",
                                  (action, None if new_answer is None else str(new_answer), reason.strip(), now(), ap_id))
            if cur.rowcount != 1:
                raise StoreError("решение по апелляции уже принято")
        return self.evaluation(ap["evaluation_id"])

    def preview(self, ev_id: int, criterion_id: str, new_answer) -> dict:
        ev = self.evaluation(ev_id)
        checklist = self.checklist(ev["checklist_key"])
        answers = dict(ev["final"]["answers"])
        answers[criterion_id] = normalize_answer(criterion_map(checklist)[criterion_id], new_answer)
        return score(checklist, answers)
