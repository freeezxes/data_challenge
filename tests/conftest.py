import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cqa.checklist import load_checklist  # noqa: E402


@pytest.fixture
def checklist():
    return load_checklist(ROOT / "config/checklist.default.json")


@pytest.fixture
def chat_record():
    return {
        "conversation_id": "t-ru-001", "channel": "agent chat", "language": "RU", "audio_url": None,
        "transcript": [
            {"timestamp": "00:00", "speaker": "client", "text": "Почему на счёте ограничение на вывод?"},
            {"timestamp": "00:05", "speaker": "bot", "text": "Подключаю оператора."},
            {"timestamp": "00:10", "speaker": "system", "text": "Эскалация на оператора."},
            {"timestamp": "01:10", "speaker": "operator", "text": "Здравствуйте, меня зовут Алина. Подтвердим личность."},
            {"timestamp": "01:30", "speaker": "system", "text": "Личность клиента подтверждена."},
            {"timestamp": "05:00", "speaker": "operator", "text": "Спасибо. Передала вопрос специалистам."},
            {"timestamp": "05:20", "speaker": "client", "text": "Спасибо"},
            {"timestamp": "05:25", "speaker": "system", "text": "Клиент покинул чат."},
        ],
    }


def all_answers(checklist, **override):
    base = {}
    for c in checklist["criteria"]:
        if c["type"] == "scale":
            base[c["id"]] = max(s["value"] for s in c["scale"])
        elif c["type"] == "critical":
            base[c["id"]] = "no"
        else:
            base[c["id"]] = "yes"
    base.update(override)
    return base


class FakeAPI:
    """Подменяет anthropic-клиент: отдаёт заранее заданные JSON-ответы по очереди."""

    def __init__(self, payloads, model="claude-opus-5-5"):
        self.payloads = list(payloads)
        self.calls = []
        self.model = model
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        return SimpleNamespace(
            stop_reason="end_turn", model=self.model,
            content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))],
            usage=SimpleNamespace(input_tokens=2000, output_tokens=3000,
                                  cache_read_input_tokens=8000, cache_creation_input_tokens=0),
        )


def model_payload(checklist, answers, evidence=("m4", "m5", "m6", "m8"), violations=None, quote="меня зовут Алина"):
    crit = {}
    for c in checklist["criteria"]:
        a = answers[c["id"]]
        item = {"evidence_ids": list(evidence), "checked_range": "m4–m6", "quote": quote,
                "reasoning": "тестовое обоснование", "answer": str(a)}
        if c["type"] == "critical":
            item["violations"] = violations or []
        item.update(facts_for(c, a))
        crit[c["id"]] = item
    return {"criteria": crit, "summary": "тест"}


def facts_for(criterion, answer):
    """Факты, из которых cqa.derive выведет тот же ответ."""
    from cqa.derive import kind_of
    k = kind_of(criterion); a = str(answer)
    if k == "needs":
        qs = {"yes": [{"id": "m4", "kind": "needed"}], "no": [{"id": "m4", "kind": "known_repeat"}]}.get(a, [])
        return {"request_formulated_before_operator": True, "operator_questions": qs, "answered_by_guess": False, "ignored_request": False}
    if k == "consult":
        st = {"20": [("closed", True)], "15": [("closed", True), ("ignored", False)], "10": [("closed", True), ("ignored", True)],
              "5": [("partial", True)], "0": [("wrong", True)], "na": []}[a]
        return {"client_questions": [{"id": "m4", "essential": e, "status": x} for x, e in st]}
    if k == "flow":
        gl = {"10": [], "7": [{"id": "m4", "type": "missed_question", "client_confused": False}],
              "4": [{"id": "m4", "type": "unclear_wording", "client_confused": True}],
              "0": [{"id": "m4", "type": "contradiction", "client_confused": False}]}[a]
        return {"glitches": gl}
    return {}
