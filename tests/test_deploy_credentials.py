"""BYOK HTTP boundary, concurrent users and queued jobs, without network model calls."""
import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import pytest

from app.server import App, make_handler
from conftest import FakeAPI, all_answers, model_payload

KEY_A = "sk-ant-test-user-a-" + "a" * 32
KEY_B = "sk-ant-test-user-b-" + "b" * 32


@pytest.fixture
def deployment(tmp_path, monkeypatch, checklist):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-key-must-never-be-used")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "server-token-must-never-be-used")
    clients = {}

    def factory(**kwargs):
        assert kwargs["base_url"] == "https://api.anthropic.com"
        assert "auth_token" not in kwargs
        key = kwargs["api_key"]
        fake = FakeAPI([model_payload(checklist, all_answers(checklist))])
        clients[key] = fake
        return fake

    monkeypatch.setattr("anthropic.Anthropic", factory)
    app = App(tmp_path / "deploy.db", workers=1)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(path, body=None, key=None):
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["X-CQA-API-Key"] = key
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}" + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers)
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    yield app, call, clients, tmp_path
    server.shutdown()
    server.server_close()
    app.pool.shutdown(wait=True)


def test_server_key_is_ignored_and_missing_key_does_not_create_job(deployment):
    app, call, clients, _ = deployment
    status, state = call("/api/state")
    assert status == 200 and state["credential_mode"] == "per_request" and not state["has_key"]
    assert state["datasets"][0]["count"] == 65
    for path in ("/api/jobs", "/api/copilot/hint", "/api/copilot/client", "/api/copilot/finish",
                 "/api/operators/plan", "/api/evaluations/1/coach", "/api/evaluations/1/variant",
                 "/api/appeals/1/assist"):
        status, result = call(path, {})
        assert status == 400 and "API-ключ" in result["error"]
    assert app.store.jobs() == [] and clients == {}


def test_credentials_do_not_cross_requests_or_concurrent_users(deployment):
    _, call, clients, _ = deployment
    keys = [KEY_A, None, KEY_B, None] * 5
    with ThreadPoolExecutor(max_workers=8) as pool:
        states = list(pool.map(lambda key: call("/api/state", key=key)[1], keys))
    assert [s["has_key"] for s in states] == [bool(key) for key in keys]
    assert all(KEY_A not in json.dumps(s) and KEY_B not in json.dumps(s) for s in states)
    assert call("/api/state")[1]["has_key"] is False
    assert clients == {}  # Attaching a key never probes a paid model endpoint.


def test_each_background_job_keeps_its_own_client_without_persisting_key(deployment):
    app, call, clients, tmp_path = deployment
    jobs = []
    for key, cid in ((KEY_A, "p1-ru-001"), (KEY_B, "p1-ru-002")):
        status, job = call("/api/jobs", {"dataset_id": "DEV", "conversation_ids": [cid], "votes": 1}, key)
        assert status == 200
        jobs.append(job["id"])
    deadline = time.monotonic() + 10
    while any(app.store.job(job)["status"] == "running" for job in jobs):
        assert time.monotonic() < deadline
        time.sleep(.02)
    assert all(app.store.job(job)["done"] == 1 for job in jobs)
    assert clients[KEY_A].calls and clients[KEY_B].calls
    assert call("/api/state")[1]["has_key"] is False
    for file in tmp_path.iterdir():
        assert KEY_A.encode() not in file.read_bytes() and KEY_B.encode() not in file.read_bytes()


def test_invalid_key_is_not_reflected_or_used(deployment):
    _, call, clients, _ = deployment
    bad = "some-private-value-not-a-claude-key"
    status, result = call("/api/state", key=bad)
    assert status == 400 and bad not in json.dumps(result)
    assert clients == {}


def test_model_error_redacts_key_in_database_and_http(deployment, monkeypatch, capsys):
    app, call, _, tmp_path = deployment

    class BrokenClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("Authentication failed: " + KEY_A)

    monkeypatch.setattr("anthropic.Anthropic", lambda **kwargs: BrokenClient())
    status, result = call("/api/copilot/hint", {"messages": []}, KEY_A)
    assert status == 500 and KEY_A not in json.dumps(result)
    status, job = call("/api/jobs", {"dataset_id": "DEV", "conversation_ids": ["p1-ru-001"], "votes": 1}, KEY_A)
    assert status == 200
    deadline = time.monotonic() + 10
    while app.store.job(job["id"])["status"] == "running":
        assert time.monotonic() < deadline
        time.sleep(.02)
    assert app.store.job(job["id"])["failed"] == 1
    for file in tmp_path.iterdir():
        assert KEY_A.encode() not in file.read_bytes()
    assert KEY_A not in capsys.readouterr().err


def test_real_sdk_uses_only_the_request_credential_without_network(tmp_path, monkeypatch):
    import anthropic
    import httpx2
    from app.credentials import request_key

    monkeypatch.setenv("ANTHROPIC_API_KEY", "environment-api-key-must-not-be-used")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "environment-token-must-not-be-used")
    constructor = anthropic.Anthropic
    observed = []

    def handler(request):
        # SDK versions use either x-api-key or a bearer credential.
        assert (request.headers.get("x-api-key") == KEY_A
                or request.headers.get("authorization") == "Bearer " + KEY_A)
        assert all("environment-" not in value for value in request.headers.values())
        observed.append(request.url.path)
        return httpx2.Response(200, json={"data": [], "has_more": False, "first_id": None, "last_id": None})

    def offline_client(**kwargs):
        return constructor(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

    monkeypatch.setattr(anthropic, "Anthropic", offline_client)
    app = App(tmp_path / "sdk.db")
    context = request_key.set(KEY_A)
    try:
        with app.client() as client:
            client.models.list()
    finally:
        request_key.reset(context)
        app.pool.shutdown()
        app.store.db.close()
    assert observed == ["/v1/models"]
