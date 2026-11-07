from datetime import datetime

from cqa.dataset import dataset_url, normalize, parse_timestamp, render_transcript
from cqa.facts import extract_facts, render_facts


def test_parse_timestamps():
    assert parse_timestamp("01:15") == 75.0
    assert parse_timestamp("1:02:03") == 3723.0
    ts = parse_timestamp("2026-11-03T13:10:00.6388245+00:00")
    assert isinstance(ts, datetime) and ts.microsecond == 638824


def test_normalize_iso_offsets():
    rec = {"conversation_id": "c", "channel": "voice call", "language": "RU", "audio_url": "u",
           "transcript": [{"timestamp": "2026-11-03T13:10:00.5+00:00", "speaker": "system", "text": "a"},
                          {"timestamp": "2026-11-03T13:11:05.5+00:00", "speaker": "operator", "text": "b"}]}
    conv = normalize(rec)
    assert [m["id"] for m in conv["messages"]] == ["m1", "m2"]
    assert conv["messages"][1]["t"] == 65.0
    assert conv["messages"][1]["timestamp"] == "2026-11-03T13:11:05.5+00:00"  # исходная метка сохраняется


def test_dataset_url_replaces_group():
    url = dataset_url("https://x.test/invoke?api-version=1&sig=abc&group_id=DEV", "CTRL")
    assert "group_id=CTRL" in url and "group_id=DEV" not in url and "sig=abc" in url


def test_facts(chat_record):
    f = extract_facts(normalize(chat_record))
    assert f["escalation_id"] == "m3"
    assert f["first_operator_id"] == "m4" and f["first_response_s"] == 60
    assert f["identity_confirmed_ids"] == ["m5"]
    assert f["final_event"] == {"type": "client_left", "id": "m8"}
    assert f["client_messages_before_operator"] == ["m1"]
    assert f["long_operator_gaps"] == [{"id": "m6", "seconds": 210, "warned_before": False}]
    h = f["heuristic_first_reply"]
    assert h["greeting_in_client_language"] and h["introduced_by_name"] and not h["informal_greeting"]
    text = render_facts(f)
    assert "Подтверждение личности: m5" in text and "client_left" in text


def test_informal_greeting_detected(chat_record):
    chat_record["transcript"][3]["text"] = "Привет, где ищете?"
    h = extract_facts(normalize(chat_record))["heuristic_first_reply"]
    assert h["informal_greeting"] and not h["introduced_by_name"]


def test_render_transcript(chat_record):
    text = render_transcript(normalize(chat_record))
    assert text.splitlines()[3].startswith("m4 [01:10] OPERATOR: Здравствуйте")


def test_event_patterns_survive_paraphrase():
    """Факты не должны зависеть от точной формулировки системного сообщения."""
    from cqa.facts import END_EVENTS, ESCALATION, IDENTITY
    for t in ("Личность клиента подтверждена.", "Идентификация клиента успешно пройдена", "Клиент верифицирован",
              "Клиенттің жеке басы расталды.", "Customer identity verified.", "Identity confirmed", "Verification completed"):
        assert IDENTITY.search(t), t
    for t in ("Эскалация на оператора.", "Чат переведён на оператора", "Оператор подключился к чату", "Чат операторға жіберілді.",
              "Chat transferred to an agent.", "Escalated to operator", "Agent joined the chat"):
        assert ESCALATION.search(t), t
    kinds = {t: k for k, rx in END_EVENTS for t in (
        "Клиент покинул чат.", "Клиент вышел из чата", "Customer left the chat.", "Client has left the conversation",
        "Соединение с клиентом прервано.", "Связь потеряна", "Connection lost", "Оператор закрыл чат.", "Чат завершён оператором",
        "Agent closed the chat.", "Клиент завершил разговор.", "Customer hung up the call") if rx.search(t)}
    assert kinds["Клиент вышел из чата"] == "client_left" and kinds["Client has left the conversation"] == "client_left"
    assert kinds["Связь потеряна"] == "connection_lost" and kinds["Чат завершён оператором"] == "operator_closed"
    assert kinds["Customer hung up the call"] == "client_ended_call" and len(kinds) == 12


def test_checklist_pairs_set_is_consistent():
    """Набор пар из чек-листа: каждая пара различается ровно одним смыслом, ожидания заданы до прогона."""
    import json
    d = json.loads((ROOT / "data/calibration/checklist-pairs.json").read_text(encoding="utf-8"))["data"]["records"]
    e = json.loads((ROOT / "data/calibration/checklist-pairs.expected.json").read_text(encoding="utf-8"))
    ids = {r["conversation_id"] for r in d}
    assert ids == set(e["expected"]) and len(d) == 16
    for a, b in e["pairs"]:
        assert a in ids and b in ids
        ea, eb = {k: v for k, v in e["expected"][a].items() if k.startswith("c")}, {k: v for k, v in e["expected"][b].items() if k.startswith("c")}
        assert set(ea) & set(eb), (a, b)  # пара проверяет один и тот же критерий
        assert any(ea[k] != eb[k] for k in set(ea) & set(eb)), (a, b)  # и ожидает разные ответы
