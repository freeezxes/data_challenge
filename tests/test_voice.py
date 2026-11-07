"""Голос оператора по настоящей записи DEV: метрики в разумных пределах, отрезки по репликам."""
import json
from pathlib import Path

import pytest

from conftest import ROOT
from cqa.dataset import normalize

pytest.importorskip("numpy")


def _conv(cid):
    rec = next(r for r in json.loads((ROOT / "public/dataset.json").read_text(encoding="utf-8"))["data"]["records"]
               if r["conversation_id"] == cid)
    return normalize(rec)


def test_operator_voice_metrics_are_plausible():
    from app.voice import VoiceUnavailable, analyze
    try:
        r = analyze(_conv("p4-ru-003"), ROOT / "public/recordings/p4-ru-003.mp3")
    except VoiceUnavailable:
        pytest.skip("нет декодера mp3 (miniaudio или ffmpeg)")
    o = r["operator"]
    assert 380 < r["duration"] < 400 and len(r["peaks"]) == 1200
    assert 100 <= o["wpm"] <= 260 and 0.2 < o["talk_share"] < 0.8
    assert 70 <= o["pitch_median_hz"] <= 300 and 0.5 < o["intonation_range_st"] < 6
    roles = {s["role"] for s in r["segments"]}
    assert {"operator", "client"} <= roles
    starts = [s["start"] for s in r["segments"]]
    assert starts == sorted(starts) and all(s["end"] > s["start"] for s in r["segments"])


def test_voice_report_is_cached(tmp_path):
    from app.voice import VoiceUnavailable, voice_report
    try:
        a = voice_report(_conv("p4-ru-001"), "/recordings/p4-ru-001.mp3", ROOT / "public", tmp_path)
    except VoiceUnavailable:
        pytest.skip("нет декодера mp3")
    assert list(tmp_path.glob("*.json")) and voice_report(_conv("p4-ru-001"), "/recordings/p4-ru-001.mp3", ROOT / "public", tmp_path) == a
