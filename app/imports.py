"""Проверка и предпросмотр импорта. Существующие транскрипты не перезаписываются."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import re
import secrets
import threading
import time

from cqa.dataset import normalize, parse_timestamp
from .store import StoreError, now


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def unpack(payload):
    if isinstance(payload, str):
        try:
            payload = json.loads(payload.lstrip("\ufeff"))
        except json.JSONDecodeError as exc:
            raise StoreError(f"JSON: {exc.msg}, строка {exc.lineno}, столбец {exc.colno}") from exc
    if isinstance(payload, dict) and payload.get("isSuccess") is False:
        raise StoreError("В файле ответ API с ошибкой. Получите набор ещё раз.")
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    group = payload.get("dataset_group_id") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and "records" in payload:
        records = payload["records"]
        declared = payload.get("total_records")
        if declared is not None and isinstance(records, list) and (type(declared) is not int or declared != len(records)):
            raise StoreError("total_records не совпадает с числом записей. Проверьте, что файл получен целиком.")
    elif isinstance(payload, dict) and "conversation_id" in payload:
        records = [payload]
    else:
        records = payload
    if not isinstance(records, list):
        raise StoreError("Нужен ответ API, объект data, массив records или один диалог с conversation_id и transcript.")
    return records, str(group) if group is not None else None


def inspect_records(records):
    errors, rows, seen = [], [], set()
    if not records:
        errors.append("В файле нет диалогов.")
    for index, record in enumerate(records, 1):
        label = f"Диалог {index}"
        before = len(errors)
        if not isinstance(record, dict):
            errors.append(f"{label}: ожидается объект.")
            continue
        cid = record.get("conversation_id")
        if not isinstance(cid, str) or not cid.strip():
            errors.append(f"{label}: отсутствует строка conversation_id.")
        else:
            label = f"Диалог {cid}"
            if cid in seen:
                errors.append(f"{label}: ID повторяется в файле. Оставьте одну запись.")
            seen.add(cid)
        if record.get("channel") not in ("agent chat", "voice call"):
            errors.append(f"{label}: channel должен быть agent chat или voice call.")
        if record.get("language") not in ("RU", "KZ", "EN"):
            errors.append(f"{label}: language должен быть RU, KZ или EN.")
        transcript = record.get("transcript")
        if isinstance(transcript, str):
            try:
                transcript = json.loads(transcript)
            except json.JSONDecodeError:
                transcript = None
        if not isinstance(transcript, list) or not transcript:
            errors.append(f"{label}: transcript должен содержать непустой массив реплик.")
            continue
        for n, message in enumerate(transcript, 1):
            loc = f"{label}, реплика {n}"
            if not isinstance(message, dict):
                errors.append(f"{loc}: ожидается объект.")
                continue
            if message.get("speaker") not in ("client", "operator", "bot", "system"):
                errors.append(f"{loc}: speaker должен быть client, operator, bot или system.")
            if not isinstance(message.get("text"), str):
                errors.append(f"{loc}: отсутствует строка text.")
            ts = message.get("timestamp")
            try:
                if not isinstance(ts, str) or not ts.strip():
                    raise ValueError()
                if "T" not in ts and not re.fullmatch(r"\d+:\d{2}(?::\d{2})?", ts):
                    raise ValueError()
                if "T" not in ts:
                    parts = list(map(int, ts.split(":")))
                    if parts[-1] >= 60 or (len(parts) == 3 and parts[1] >= 60):
                        raise ValueError()
                parse_timestamp(ts)
            except (ValueError, TypeError):
                errors.append(f"{loc}: timestamp должен быть MM:SS, H:MM:SS или ISO 8601.")
        if len(errors) == before:
            try:
                normalize(record)  # включая совместимость форматов времени внутри одного диалога
            except (ValueError, TypeError, KeyError) as exc:
                errors.append(f"{label}: несовместимые метки времени ({type(exc).__name__}).")
                continue
            rows.append({"id": cid, "language": record["language"], "channel": record["channel"],
                         "messages": len(transcript), "sample": deepcopy(transcript[:3])})
    return errors, rows


class ImportManager:
    ttl = 30 * 60

    def __init__(self, store):
        self.store = store
        self.plans = {}
        self.lock = threading.RLock()

    def preview(self, payload, name="", append_to=None, source="upload"):
        records, group = unpack(payload)
        errors, rows = inspect_records(records)
        name = str(name or group or "Загруженные диалоги").strip() or "Загруженные диалоги"
        if len(name) > 160:
            errors.append("Название набора должно быть не длиннее 160 символов.")
        with self.store.lock:
            existing = {}
            if append_to:
                if not any(d["id"] == append_to for d in self.store.datasets()):
                    raise StoreError("Выбранный набор больше не существует. Выберите набор заново.")
                existing = {r["conversation_id"]: r for r in self.store.records(append_to)}
                name = next(d["name"] for d in self.store.datasets() if d["id"] == append_to)
            incoming = {r.get("conversation_id"): r for r in records if isinstance(r, dict) and isinstance(r.get("conversation_id"), str)}
            for row in rows:
                row["conflict"] = row["id"] in existing
                row["changed"] = row["conflict"] and digest(existing[row["id"]]) != digest(incoming[row["id"]])
            conflicts = sum(r["conflict"] for r in rows)
            result = {"name": name, "append_to": append_to, "source_group": group, "total": len(records),
                      "new": len(rows) - conflicts, "conflicts": conflicts,
                      "changed": sum(r["changed"] for r in rows), "errors": errors,
                      "languages": dict(Counter(r["language"] for r in rows)),
                      "channels": dict(Counter(r["channel"] for r in rows)), "rows": rows}
            if errors:
                return result
            plan = {"records": deepcopy(records), "group": group, "name": name, "append_to": append_to,
                    "source": source, "snapshot": digest(list(existing.values())), "conflicts": conflicts,
                    "created": time.monotonic()}
        with self.lock:
            self.plans = {k: v for k, v in self.plans.items() if time.monotonic() - v["created"] < self.ttl}
            if len(self.plans) >= 32:
                self.plans.pop(next(iter(self.plans)))
            token = secrets.token_urlsafe(24)
            self.plans[token] = plan
        return {**result, "token": token}

    def commit(self, token, skip_existing=False):
        with self.lock, self.store.lock:
            p = self.plans.get(token)
            if not p or time.monotonic() - p["created"] >= self.ttl:
                raise StoreError("Предпросмотр истёк. Проверьте файл ещё раз.")
            ds_id = p["append_to"]
            if ds_id:
                existing = {r["conversation_id"]: r for r in self.store.records(ds_id)}
                if digest(list(existing.values())) != p["snapshot"]:
                    raise StoreError("Набор изменился после предпросмотра. Проверьте импорт ещё раз.")
                if p["conflicts"] and not skip_existing:
                    raise StoreError("Есть совпадающие ID. Подтвердите их пропуск или создайте новый набор.")
            else:
                existing = {}
                base = re.sub(r"[^\w.-]+", "-", p["group"] or p["name"], flags=re.UNICODE).strip("-")[:40] or "upload"
                ds_id = base
                n = 2
                while self.store.q("select 1 from datasets where id=?", (ds_id,)):
                    ds_id = f"{base}-{n}"
                    n += 1
            records = [r for r in p["records"] if r["conversation_id"] not in existing]
            with self.store.db:
                if not p["append_to"]:
                    self.store.db.execute("insert into datasets(id,name,source,created_at) values(?,?,?,?)",
                                          (ds_id, p["name"], p["source"], now()))
                pos = self.store.q("select max(position) p from conversations where dataset_id=?", (ds_id,))[0]["p"]
                start = 0 if pos is None else pos + 1
                self.store.db.executemany("insert into conversations(dataset_id,conversation_id,position,record) values(?,?,?,?)",
                                         [(ds_id, r["conversation_id"], start + i, json.dumps(r, ensure_ascii=False))
                                          for i, r in enumerate(records)])
            del self.plans[token]
            return {"id": ds_id, "count": len(records), "skipped": len(p["records"]) - len(records),
                    "ids": [r["conversation_id"] for r in records]}
