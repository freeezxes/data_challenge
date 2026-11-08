"""SSRF/redirect boundaries and account-owned audio report caches; no external calls."""
import copy
import socket
from pathlib import Path

import pytest

from app.voice import VoiceUnavailable, fetch_audio, voice_report
from conftest import ROOT
from cqa.dataset import normalize


@pytest.mark.parametrize("source", ["file:///etc/passwd", "http://127.0.0.1:8000/private",
                                     "https://127.0.0.1/private", "https://[::1]/private",
                                     "https://100.75.211.119/private", "https://169.254.169.254/latest",
                                     "https://user:password@example.com/audio.wav", "https://example.com:8100/a"])
def test_unsafe_audio_sources_are_rejected_before_any_read(source, tmp_path, monkeypatch):
    def never_connect(*args, **kwargs):
        raise AssertionError("Unsafe source must never open a socket")
    monkeypatch.setattr(socket, "create_connection", never_connect)
    with pytest.raises(VoiceUnavailable):
        fetch_audio(source, ROOT / "public", tmp_path)


def test_public_name_resolving_to_private_address_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(VoiceUnavailable):
        fetch_audio("https://public-looking.example/audio.wav", ROOT / "public", tmp_path)


def test_redirect_to_private_target_is_rechecked(tmp_path, monkeypatch):
    import app.voice as voice
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1" if host == "internal.example" else "93.184.216.34", 443))])
    connections = []
    class FakeConnection:
        def __init__(self, hostname, address): connections.append((hostname, address))
        def request(self, *args, **kwargs): pass
        def getresponse(self): return self
        status = 302
        def getheader(self, name): return "https://internal.example/audio.wav" if name == "Location" else None
        def close(self): pass
    monkeypatch.setattr(voice, "_PinnedHTTPS", FakeConnection)
    with pytest.raises(VoiceUnavailable):
        fetch_audio("https://public.example/audio.wav", ROOT / "public", tmp_path)
    assert connections == [("public.example", "93.184.216.34")]
    assert not list(tmp_path.glob("*.tmp"))


def test_audio_fingerprint_changes_with_roles_and_bytes(tmp_path, monkeypatch):
    import app.voice as voice
    audio = tmp_path / "audio.wav"; audio.write_bytes(b"audio-one")
    monkeypatch.setattr(voice, "fetch_audio", lambda *args: audio)
    monkeypatch.setattr(voice, "analyze", lambda conv, path: {"role": conv["messages"][0]["role"], "bytes": path.read_text()})
    conv = {"conversation_id": "same-id", "messages": [{"id": "m1", "t": 0, "role": "client", "text": "original"}]}
    a = voice_report(conv, "/recordings/test.wav", ROOT / "public", tmp_path / "reports")
    other = copy.deepcopy(conv); other["messages"][0]["role"] = "operator"
    b = voice_report(other, "/recordings/test.wav", ROOT / "public", tmp_path / "reports")
    assert a != b and b["role"] == "operator"
    audio.write_bytes(b"audio-two")  # same byte length, different content
    c = voice_report(other, "/recordings/test.wav", ROOT / "public", tmp_path / "reports")
    assert b != c
    assert len(list((tmp_path / "reports").glob("*.json"))) == 3


def test_workspaces_have_distinct_persistent_audio_caches(tmp_path):
    from app.server import App
    a, b = App(tmp_path / "a.db"), App(tmp_path / "b.db")
    try:
        assert a.voice_cache != b.voice_cache
        assert tmp_path in a.voice_cache.parents and tmp_path in b.voice_cache.parents
    finally:
        for app in (a, b):
            app.pool.shutdown(); app.store.db.close()
