"""«Прямой эфир» оценки: готовые критерии из ещё не дописанного JSON-ответа модели.

Ответ модели — объект {"criteria": {"c01": {...}, ...}, "summary": "..."}. Пока он пишется,
выбираем те объекты критериев, у которых уже есть закрывающая скобка, и показываем их сразу.
Окончательная оценка (все прогоны, голосование, проверка доказательств) приходит позже, как обычно.
"""
from __future__ import annotations

import json
import threading
import time

from cqa.checklist import normalize_answer
from cqa.derive import derive, kind_of


def _string_end(buf: str, i: int) -> int:
    """Индекс закрывающей кавычки строки, начинающейся в buf[i] == '"'; -1 — строка не дописана."""
    i += 1
    while i < len(buf):
        if buf[i] == "\\":
            i += 2
            continue
        if buf[i] == '"':
            return i
        i += 1
    return -1


def _object_end(buf: str, i: int) -> int:
    """Индекс закрывающей '}' объекта, начинающегося в buf[i] == '{'; -1 — не дописан."""
    depth = 0
    while i < len(buf):
        ch = buf[i]
        if ch == '"':
            i = _string_end(buf, i)
            if i < 0:
                return -1
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def partial_criteria(buf: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    k = buf.find('"criteria"')
    if k < 0:
        return out
    i = buf.find("{", k)
    if i < 0:
        return out
    i += 1
    while True:
        while i < len(buf) and buf[i] in " \n\r\t,":
            i += 1
        if i >= len(buf) or buf[i] != '"':
            return out
        e = _string_end(buf, i)
        if e < 0:
            return out
        key = buf[i + 1:e]
        j = buf.find("{", e)
        if j < 0:
            return out
        end = _object_end(buf, j)
        if end < 0:
            return out
        try:
            out[key] = json.loads(buf[j:end + 1])
        except json.JSONDecodeError:
            return out
        i = end + 1


class LiveBoard:
    """Частичные ответы текущих оценок: (набор, диалог) → текст. Живут, пока идёт оценка."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items: dict[tuple[str, str], dict] = {}

    def start(self, ds, cid, checklist_key):
        with self.lock:
            self.items[(ds, cid)] = {"text": "", "started": time.time(), "checklist_key": checklist_key}

    def update(self, ds, cid, text):
        with self.lock:
            if (ds, cid) in self.items:
                self.items[(ds, cid)]["text"] = text

    def finish(self, ds, cid):
        with self.lock:
            self.items.pop((ds, cid), None)

    def view(self, ds, cid, checklist: dict | None) -> dict:
        with self.lock:
            item = dict(self.items.get((ds, cid)) or {})
        if not item:
            return {"state": "none"}
        crit = partial_criteria(item["text"])
        shown = {}
        for c in (checklist or {}).get("criteria", []):
            d = crit.get(c["id"])
            if not d:
                continue
            kind = kind_of(c)
            ans = derive(kind, d, c) if kind else None
            try:
                ans = normalize_answer(c, ans if ans is not None else d.get("answer"))
            except (ValueError, TypeError):
                ans = d.get("answer")
            shown[c["id"]] = {"answer": ans, "reasoning": d.get("reasoning", ""), "quote": d.get("quote", ""),
                              "evidence_ids": d.get("evidence_ids", [])}
        return {"state": "writing" if item["text"] else "thinking", "elapsed_s": round(time.time() - item["started"], 1),
                "checklist_key": item["checklist_key"], "criteria": shown, "chars": len(item["text"])}
