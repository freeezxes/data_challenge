"""Deployment auth boundary and persistence through real HTTP, with fake model only."""
import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import pytest

from app.auth import Accounts, AuthError
from app.server import App, make_handler
from conftest import FakeAPI, all_answers, model_payload

PASSWORD = "a private test password 123!"
KEY_A = "sk-ant-test-account-a-" + "a" * 32
KEY_B = "sk-ant-test-account-b-" + "b" * 32


class Browser:
    def __init__(self, base):
        self.base, self.csrf, self.user_id = base, "", ""
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def call(self, path, body=None, headers=None, raw=False):
        values = {}
        if body is not None:
            values.update({"Content-Type": "application/json", "X-CQA-Request": "1", "X-CQA-CSRF": self.csrf})
        if self.user_id:
            values["X-CQA-Account"] = self.user_id
        values.update(headers or {})
        request = urllib.request.Request(self.base + path, headers=values,
                                        data=json.dumps(body).encode() if body is not None else None)
        try:
            response = self.opener.open(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = response.read()
            if raw:
                return response.status, data
            data = json.loads(data)
            if response.status == 200 and path in ("/api/auth/login", "/api/auth/register", "/api/auth/session"):
                self.csrf, self.user_id = data["csrf"], data["user"]["id"]
            return response.status, data

    def register(self, name):
        status, data = self.call("/api/auth/register", {"username": name, "password": PASSWORD})
        assert status == 200, data
        return data


@contextmanager
def site(directory, guest=False):
    app = App(directory / "legacy.db", workers=2)
    # Existing anonymous imports must be retained for the administrator.
    app.store.save_dataset("existing-import", "Existing private import", [app.store.record("DEV", "p1-ru-001")], "test")
    accounts = Accounts(directory / "auth", app, lambda path: App(path, pool=app.pool))
    guest_app = App(directory / "guest/cqa.db", pool=app.pool) if guest else None
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, accounts, guest_app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield accounts, app, base
    finally:
        server.shutdown()
        server.server_close()
        app.pool.shutdown(wait=True)
        for tenant in accounts.apps.values():
            tenant.store.db.close()
        app.store.db.close()
        accounts.db.close()


def test_guest_access_never_exposes_account_data_or_keys(tmp_path):
    with site(tmp_path, guest=True) as (accounts, app, base):
        visitor = Browser(base)
        session = visitor.call("/api/auth/session")[1]
        assert session['guest'] and not session['has_key']
        assert visitor.call("/", raw=True)[0] == 200
        assert visitor.call("/api/health")[1]['auth_required'] is False
        assert 'existing-import' not in {d['id'] for d in visitor.call('/api/state')[1]['datasets']}
        assert visitor.call('/api/conversations/existing-import/p1-ru-001')[0] == 400
        assert visitor.call('/api/auth/key', {'key': KEY_A})[0] == 401
        assert visitor.call('/api/jobs', {'dataset_id':'DEV','conversation_ids':['p1-ru-001'],'votes':1})[0] == 400
        assert visitor.call('/api/state', headers={'X-CQA-Account':'old-private-account'})[0] == 401
        credentials = dict(line.split(': ', 1) for line in (accounts.directory / 'admin-login.txt').read_text().splitlines())
        assert visitor.call('/api/auth/login', {'username':'admin','password':credentials['Password']})[0] == 200
        assert 'existing-import' in {d['id'] for d in visitor.call('/api/state')[1]['datasets']}


@pytest.fixture
def service(tmp_path, monkeypatch, checklist):
    clients = {}

    def factory(**kwargs):
        assert kwargs["base_url"] == "https://api.anthropic.com" and "auth_token" not in kwargs
        key = kwargs["api_key"]
        fake = FakeAPI([model_payload(checklist, all_answers(checklist))])
        clients[key] = fake
        return fake

    monkeypatch.setattr("anthropic.Anthropic", factory)
    with site(tmp_path) as (accounts, app, base):
        yield accounts, app, base, clients


def test_anonymous_api_and_data_are_closed(service):
    _, _, base, clients = service
    browser = Browser(base)
    for path in ("/api/state", "/api/datasets/DEV/conversations", "/api/jobs", "/api/operators",
                 "/api/appeals", "/api/live/DEV/p1-ru-001", "/api/checklists", "/api/auth/session"):
        assert browser.call(path)[0] == 401, path
    for path in ("/dataset.json", "/annotations.json", "/viewer.html", "/recordings/p4-ru-001.mp3"):
        assert browser.call(path, raw=True)[0] == 401, path
    assert browser.call("/login", raw=True)[0] == 200
    assert browser.call("/js/auth.js", raw=True)[0] == 200
    assert browser.call("/api/health")[1]["auth_required"] is True
    assert not clients


def test_admin_retains_previous_imports_and_registration_is_private(service):
    accounts, app, base, _ = service
    credentials = dict(line.split(": ", 1) for line in (accounts.directory / "admin-login.txt").read_text().splitlines())
    admin, alice = Browser(base), Browser(base)
    assert admin.call("/api/auth/login", {"username": "admin", "password": credentials["Password"]})[0] == 200
    assert "existing-import" in {d["id"] for d in admin.call("/api/state")[1]["datasets"]}
    alice.register("alice")
    assert "existing-import" not in {d["id"] for d in alice.call("/api/state")[1]["datasets"]}
    assert app.store.datasets()  # Adoption never clears the legacy database.
    assert alice.call("/api/auth/register", {"username": "admin", "password": PASSWORD})[0] == 400


def test_imports_preview_tokens_keys_and_ids_are_account_scoped(service):
    accounts, app, base, clients = service
    alice, bob = Browser(base), Browser(base)
    alice.register("alice"); bob.register("bob")
    record = app.store.record("DEV", "p1-ru-001")
    status, preview = alice.call("/api/imports/preview", {"name": "Alice only", "payload": record})
    assert status == 200
    assert bob.call("/api/imports/commit", {"token": preview["token"]})[0] == 400
    status, imported = alice.call("/api/imports/commit", {"token": preview["token"]})
    assert status == 200
    assert imported["id"] not in {d["id"] for d in bob.call("/api/state")[1]["datasets"]}
    assert bob.call(f"/api/datasets/{imported['id']}/conversations")[0] == 400
    assert bob.call(f"/api/datasets/{imported['id']}/export", raw=True)[0] == 400
    assert bob.call("/api/evaluations/1/trace")[0] == 400
    assert bob.call("/api/jobs/1")[1] is None
    assert bob.call("/api/appeals", {"evaluation_id": 1, "criterion_id": "c01", "comment": "Other account"})[0] == 400
    alice_user = accounts.user(alice.user_id)
    alice_app = accounts.workspace(alice_user)
    alice_app.live.start("DEV", "p1-ru-001", alice_app.store.get_setting("active_checklist"))
    alice_live = alice.call("/api/live/DEV/p1-ru-001")[1]
    bob_live = bob.call("/api/live/DEV/p1-ru-001")[1]
    assert alice_live != bob_live
    for browser, key in ((alice, KEY_A), (bob, KEY_B)):
        status, result = browser.call("/api/auth/key", {"key": key})
        assert status == 200 and result["has_key"] and key not in json.dumps(result)
    jobs = []
    for browser in (alice, bob):
        status, job = browser.call("/api/jobs", {"dataset_id": "DEV", "conversation_ids": ["p1-ru-001"], "votes": 1})
        assert status == 200
        jobs.append((browser, job["id"]))
    for browser, job_id in jobs:
        deadline = time.monotonic() + 10
        while browser.call(f"/api/jobs/{job_id}")[1]["status"] == "running":
            assert time.monotonic() < deadline
            time.sleep(.02)
    assert clients[KEY_A].calls and clients[KEY_B].calls
    # Ciphertext, not the user's key or password, is stored on disk.
    accounts.db.execute("pragma wal_checkpoint(full)")
    data = (accounts.directory / "accounts.db").read_bytes()
    assert KEY_A.encode() not in data and KEY_B.encode() not in data and PASSWORD.encode() not in data
    assert alice.call("/api/auth/key", {"key": ""})[1]["has_key"] is False
    assert bob.call("/api/auth/session")[1]["has_key"] is True
    # Ciphertexts are cryptographically bound to their owner, even if a row is mixed up.
    bob_user = accounts.user(bob.user_id)
    alice_user["api_key"] = bob_user["api_key"]
    with pytest.raises(ValueError):
        accounts.key(alice_user)


def test_logout_relogin_and_process_restart_keep_all_saved_state(tmp_path):
    with site(tmp_path) as (accounts, app, base):
        alice = Browser(base)
        alice.register("alice")
        assert alice.call("/api/auth/key", {"key": KEY_A})[0] == 200
        assert alice.call("/api/auth/preferences", {"values": {"cqa-dataset": "my-import", "cqa-role": "reviewer"}})[0] == 200
        record = app.store.record("DEV", "p1-ru-001")
        _, preview = alice.call("/api/imports/preview", {"name": "Remember me", "payload": record})
        _, imported = alice.call("/api/imports/commit", {"token": preview["token"]})
        assert alice.call("/api/auth/logout", {})[0] == 200
        assert alice.call("/api/state")[0] == 401
        assert alice.call("/api/auth/login", {"username": "alice", "password": PASSWORD})[0] == 200
        tenant = accounts.workspace(accounts.user(alice.user_id))
        unfinished = tenant.store.create_job("DEV", tenant.store.get_setting("active_checklist"), 1)
    with site(tmp_path) as (_, _, new_base):
        alice.base = new_base
        # Existing browser cookie survives a server restart.
        status, session = alice.call("/api/auth/session")
        assert status == 200 and session["has_key"]
        assert session["preferences"]["cqa-role"] == "reviewer"
        assert imported["id"] in {d["id"] for d in alice.call("/api/state")[1]["datasets"]}
        recovered = alice.call(f"/api/jobs/{unfinished}")[1]
        assert recovered["status"] == "interrupted" and recovered["finished_at"]
        fresh_device = Browser(new_base)
        assert fresh_device.call("/api/auth/login", {"username": "alice", "password": PASSWORD})[1]["has_key"]
        assert fresh_device.call("/api/auth/session")[1]["preferences"]["cqa-dataset"] == "my-import"


def test_csrf_forged_session_and_stale_account_are_rejected(service):
    accounts, _, base, clients = service
    browser = Browser(base)
    assert browser.call("/api/auth/login", {"username": "admin", "password": PASSWORD}, {"Origin": "https://attacker.invalid"})[0] == 403
    browser.register("alice")
    assert browser.call("/api/auth/key", {"key": KEY_A}, {"X-CQA-CSRF": "invalid"})[0] == 403
    assert browser.call("/api/auth/key", {"key": KEY_A}, {"Origin": "https://attacker.invalid"})[0] == 403
    assert browser.call("/api/auth/key", {"key": KEY_A}, {"X-CQA-Account": "another-account"})[0] == 409
    assert Browser(base).call("/api/state", headers={"Cookie": "cqa-session=forged"})[0] == 401
    old_csrf = browser.csrf
    browser.call("/api/auth/logout", {})
    browser.call("/api/auth/login", {"username": "alice", "password": PASSWORD})
    assert browser.call("/api/auth/key", {"key": KEY_A}, {"X-CQA-CSRF": old_csrf})[0] == 403
    assert not clients
    assert "HttpOnly" in accounts.cookie("token") and "SameSite=Lax" in accounts.cookie("token")


def test_expired_session_rate_limit_and_missing_master_fail_closed(service):
    accounts, _, base, _ = service
    browser = Browser(base); browser.register("alice")
    with accounts.lock:
        accounts.db.execute("update sessions set expires=0")
        accounts.db.commit()
    assert browser.call("/api/state")[0] == 401
    accounts._limit("test-limit", 1, 300)
    with pytest.raises(AuthError) as error:
        accounts._limit("test-limit", 1, 300)
    assert error.value.status == 429
    master = accounts.directory / "master.key"
    saved = master.read_bytes(); master.unlink()
    with pytest.raises(RuntimeError, match="Restore"):
        Accounts(accounts.directory, accounts.legacy_app, accounts.app_factory)
    master.write_bytes(saved)
