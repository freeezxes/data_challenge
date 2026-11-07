"""Загрузка набора из API организаторов и нормализация диалогов."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROLE_LABEL = {"client": "CLIENT", "operator": "OPERATOR", "bot": "BOT", "system": "SYSTEM"}


def dataset_url(base_url: str, group_id: str) -> str:
    """Подставляет group_id в адрес из гайда (адрес содержит подпись — в репозиторий не коммитим)."""
    parts = urllib.parse.urlsplit(base_url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k != "group_id"]
    query.append(("group_id", group_id))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def fetch_dataset(base_url: str, group_id: str, timeout: int = 90) -> dict:
    with urllib.request.urlopen(dataset_url(base_url, group_id), timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("isSuccess"):
        raise RuntimeError(f"API вернул ошибку: {payload.get('error')!r}")
    return payload


def load_records(path: str | Path) -> list[dict]:
    """Принимает полный ответ API, объект data или голый список записей."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict):
        data = data["records"]
    return data


_FRACTION = re.compile(r"(\.\d{6})\d+")


def parse_timestamp(ts: str) -> float | datetime:
    """'MM:SS' / 'H:MM:SS' → секунды; ISO 8601 → datetime."""
    ts = ts.strip()
    if "T" in ts:
        return datetime.fromisoformat(_FRACTION.sub(r"\1", ts).replace("Z", "+00:00"))
    parts = [int(p) for p in ts.split(":")]
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return float(seconds)


def format_offset(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def normalize(record: dict) -> dict:
    """Диалог с ID реплик m1…mN и смещением t (сек от первой реплики)."""
    raw = record.get("transcript") or []
    if isinstance(raw, str):
        raw = json.loads(raw)
    parsed = [parse_timestamp(m["timestamp"]) for m in raw]
    base = parsed[0] if parsed else 0.0
    messages = []
    for i, (m, p) in enumerate(zip(raw, parsed), start=1):
        t = (p - base).total_seconds() if isinstance(p, datetime) else p - base
        messages.append({
            "id": f"m{i}",
            "role": m["speaker"],
            "text": m["text"],
            "timestamp": m["timestamp"],
            "t": float(t),
        })
    return {
        "conversation_id": record["conversation_id"],
        "channel": record.get("channel"),
        "language": record.get("language"),
        "audio_url": record.get("audio_url"),
        "messages": messages,
    }


def render_transcript(conv: dict) -> str:
    lines = []
    for m in conv["messages"]:
        text = " ".join(m["text"].split())
        lines.append(f"{m['id']} [{format_offset(m['t'])}] {ROLE_LABEL.get(m['role'], m['role'].upper())}: {text}")
    return "\n".join(lines)
