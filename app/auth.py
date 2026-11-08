"""Persistent deployment accounts, private workspaces, sessions and encrypted BYOK."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

from .credentials import validate_key
from .store import StoreError

SESSION_TTL = 30 * 24 * 3600
PREF_KEYS = {"cqa-role", "cqa-dataset", "cqa-votes", "cqa-cp-mode", "cqa-cp-name", "cqa-team"}


class AuthError(Exception):
    def __init__(self, message, status=401):
        super().__init__(message)
        self.status = status


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=3,
                          maxmem=64 * 1024 * 1024, dklen=32).hex()


class Accounts:
    def __init__(self, directory, legacy_app, app_factory, public_origin=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.legacy_app, self.app_factory = legacy_app, app_factory
        self.public_origin = public_origin
        self.secure = bool(public_origin and urlsplit(public_origin).scheme == "https")
        self.cookie_name = "__Host-cqa-session" if self.secure else "cqa-session"
        self.lock = threading.RLock()
        self.password_slots = threading.BoundedSemaphore(2)
        self.attempts = OrderedDict()
        self.apps = {}
        keyfile = self.directory / "master.key"
        dbfile = self.directory / "accounts.db"
        # Never silently replace a lost key on an existing installation.
        if not keyfile.exists():
            if dbfile.exists():
                raise RuntimeError("Restore auth/master.key from backup before starting CQA")
            try:
                with keyfile.open("xb") as f:
                    os.chmod(keyfile, 0o600)
                    f.write(Fernet.generate_key())
            except FileExistsError:
                pass
        keyfile.chmod(0o600)
        self.master = keyfile.read_bytes()
        self.cipher = Fernet(self.master)
        self.db = sqlite3.connect(dbfile, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript("""
            create table if not exists users(
                id text primary key, username text unique not null, salt blob not null,
                password_hash text not null, is_admin integer not null default 0,
                api_key blob, preferences text not null default '{}');
            create table if not exists sessions(
                token_hash text primary key, user_id text not null, expires real not null);
            create index if not exists sessions_user on sessions(user_id);
        """)
        dbfile.chmod(0o600)
        if not self.db.execute("select 1 from users where is_admin=1").fetchone():
            password = secrets.token_urlsafe(24)
            admin = self.register("admin", password, is_admin=True)
            credential_file = self.directory / "admin-login.txt"
            with credential_file.open("x") as f:
                os.chmod(credential_file, 0o600)
                f.write(f"CQA: {public_origin or 'local deployment'}\nUsername: {admin['username']}\nPassword: {password}\n")
        self.dummy_salt = b"cqa-auth-dummy-salt"
        self.dummy_hash = password_hash("not-an-account-password", self.dummy_salt)

    def _limit(self, key, count, window):
        now = time.monotonic()
        with self.lock:
            hits = self.attempts.setdefault(key, deque())
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= count:
                raise AuthError("Слишком много попыток. Попробуйте позже.", 429)
            hits.append(now)
            self.attempts.move_to_end(key)
            while len(self.attempts) > 4096:
                self.attempts.popitem(last=False)

    def validate_write(self, headers):
        # Custom header plus JSON prevents cross-site form and fetch attacks,
        # including login CSRF. This server never enables cross-origin requests.
        if headers.get("X-CQA-Request") != "1":
            raise AuthError("Обновите страницу и повторите действие.", 403)
        if headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise AuthError("Ожидается JSON.", 415)
        origin = headers.get("Origin")
        expected = self.public_origin or f"http://{headers.get('Host')}"
        allowed = {expected}
        if self.public_origin == "https://ortaresearch.com":
            allowed.add("https://www.ortaresearch.com")
        if origin and origin not in allowed:
            raise AuthError("Запрос с другого сайта отклонён.", 403)
        if headers.get("Sec-Fetch-Site") == "cross-site":
            raise AuthError("Запрос с другого сайта отклонён.", 403)

    def register(self, username, password, is_admin=False):
        if not isinstance(username, str) or not isinstance(password, str):
            raise StoreError("Укажите имя пользователя и пароль")
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,39}", username):
            raise StoreError("Имя: 3–40 латинских букв, цифр, точек, дефисов или подчёркиваний")
        if not 12 <= len(password) <= 256:
            raise StoreError("Пароль должен содержать от 12 до 256 символов")
        salt = secrets.token_bytes(16)
        with self.password_slots:
            digest = password_hash(password, salt)
        user_id = secrets.token_hex(16)
        with self.lock:
            try:
                self.db.execute("insert into users(id,username,salt,password_hash,is_admin) values(?,?,?,?,?)",
                                (user_id, username, salt, digest, int(is_admin)))
                self.db.commit()
            except sqlite3.IntegrityError:
                self.db.rollback()
                raise StoreError("Это имя уже занято") from None
        return self.user(user_id)

    def user(self, user_id):
        with self.lock:
            row = self.db.execute("select * from users where id=?", (user_id,)).fetchone()
        if not row:
            raise AuthError("Войдите в аккаунт.")
        return dict(row)

    def login(self, username, password, address):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 100 or len(password) > 256:
            raise AuthError("Неверное имя пользователя или пароль.")
        name = username.strip().lower()
        self._limit(("login-ip", address), 30, 300)
        self._limit(("login-user", name), 10, 300)
        with self.lock:
            row = self.db.execute("select * from users where username=?", (name,)).fetchone()
        with self.password_slots:
            digest = password_hash(password, row["salt"] if row else self.dummy_salt)
        if not hmac.compare_digest(digest, row["password_hash"] if row else self.dummy_hash) or not row:
            raise AuthError("Неверное имя пользователя или пароль.")
        return dict(row)

    def new_session(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.db.execute("delete from sessions where expires <= ?", (time.time(),))
            self.db.execute("insert into sessions values(?,?,?)",
                            (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + SESSION_TTL))
            self.db.commit()
        return token

    def session(self, headers):
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("Cookie", ""))
            token = cookie[self.cookie_name].value
        except (KeyError, ValueError, CookieError):
            raise AuthError("Войдите в аккаунт.") from None
        if len(token) > 100:
            raise AuthError("Войдите в аккаунт.")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            row = self.db.execute("select user_id from sessions where token_hash=? and expires>?",
                                  (digest, time.time())).fetchone()
        if not row:
            raise AuthError("Сессия завершена. Войдите снова.")
        return self.user(row["user_id"]), token

    def csrf(self, token):
        return hmac.new(self.master, token.encode(), hashlib.sha256).hexdigest()

    def validate_csrf(self, headers, token):
        if not hmac.compare_digest(headers.get("X-CQA-CSRF", ""), self.csrf(token)):
            raise AuthError("Обновите страницу и повторите действие.", 403)

    def cookie(self, token=""):
        secure = "; Secure" if self.secure else ""
        age = SESSION_TTL if token else 0
        return f"{self.cookie_name}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}{secure}"

    def logout(self, token):
        with self.lock:
            self.db.execute("delete from sessions where token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
            self.db.commit()

    def workspace(self, user):
        if user["is_admin"]:
            return self.legacy_app
        with self.lock:
            if user["id"] not in self.apps:
                path = self.directory / "workspaces" / (user["id"] + ".db")
                self.apps[user["id"]] = self.app_factory(path)
            return self.apps[user["id"]]

    def key(self, user):
        if not user["api_key"]:
            return ""
        try:
            value = json.loads(self.cipher.decrypt(user["api_key"]))
            if value["user_id"] != user["id"]:
                raise InvalidToken
            return value["key"]
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise StoreError("Не удалось прочитать сохранённый ключ. Введите его заново.") from None

    def save_key(self, user, key):
        if not isinstance(key, str):
            raise StoreError("Ключ должен быть строкой")
        key = validate_key(key.strip())
        value = self.cipher.encrypt(json.dumps({"user_id": user["id"], "key": key}).encode()) if key else None
        with self.lock:
            self.db.execute("update users set api_key=? where id=?", (value, user["id"]))
            self.db.commit()

    def preferences(self, user, values):
        if not isinstance(values, dict) or any(k not in PREF_KEYS or not isinstance(v, str) or len(v) > 200 for k, v in values.items()):
            raise StoreError("Неверные настройки аккаунта")
        with self.lock:
            current = json.loads(self.user(user["id"])["preferences"])
            current.update(values)
            self.db.execute("update users set preferences=? where id=?", (json.dumps(current), user["id"]))
            self.db.commit()
        return current

    def public_user(self, user, token):
        try:
            key = self.key(user)
        except StoreError:
            # Login and replacement must remain usable if a saved ciphertext is damaged.
            key = ""
        return {"authenticated": True, "user": {"id": user["id"], "username": user["username"]},
                "csrf": self.csrf(token), "has_key": bool(key),
                "key_hint": "sk-ant-…" + key[-4:] if key else "",
                "preferences": json.loads(user["preferences"])}
