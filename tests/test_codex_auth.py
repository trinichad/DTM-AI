"""Codex OAuth (D-26) — token refresh + device-code sign-in flow, no network."""
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path

from execution.clients._http import HttpError
from execution.core import codex_auth
from execution.core.config import Config
from execution.core.credentials import MissingCredential
from execution.core.secrets_store import SecretStore


def _jwt(exp: int, account: str = "acct-1") -> str:
    payload = {"exp": exp, "https://api.openai.com/auth": {"chatgpt_account_id": account}}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"h.{body}.s"


def _cfg_with_store(**pairs):
    d = Path(tempfile.mkdtemp())
    env = d / ".env"
    env.write_text("", encoding="utf-8")
    env.chmod(0o600)
    store = SecretStore(path=d / "secrets.local")
    if pairs:
        store.set_many(pairs, allowed_keys=set(pairs))
    return Config(env_path=env, secret_store=store), store


class EnsureFresh(unittest.TestCase):
    def test_valid_token_returned_without_refresh(self):
        tok = _jwt(int(time.time()) + 3600)
        cfg, _ = _cfg_with_store(OPENAI_CODEX_ACCESS_TOKEN=tok, OPENAI_CODEX_REFRESH_TOKEN="rt")
        access, acct = codex_auth.ensure_fresh(cfg, transport=lambda *a, **k: self.fail("no refresh expected"))
        self.assertEqual(access, tok)
        self.assertEqual(acct, "acct-1")

    def test_expired_token_refreshes_and_persists(self):
        old = _jwt(int(time.time()) - 10)
        new = _jwt(int(time.time()) + 3600, "acct-2")
        cfg, store = _cfg_with_store(OPENAI_CODEX_ACCESS_TOKEN=old, OPENAI_CODEX_REFRESH_TOKEN="rt-old")
        captured = {}

        def fake(method, url, headers=None, params=None, json_body=None, **kw):
            captured["url"] = url
            captured["body"] = json_body
            captured["ua"] = (headers or {}).get("User-Agent", "")
            return 200, {"access_token": new, "refresh_token": "rt-new"}

        access, acct = codex_auth.ensure_fresh(cfg, transport=fake)
        self.assertEqual(access, new)
        self.assertEqual(acct, "acct-2")
        self.assertEqual(captured["body"]["grant_type"], "refresh_token")
        self.assertTrue(captured["ua"])               # Cloudflare blocks the default urllib UA
        # rotated tokens persisted to the store
        self.assertEqual(store.get("OPENAI_CODEX_ACCESS_TOKEN"), new)
        self.assertEqual(store.get("OPENAI_CODEX_REFRESH_TOKEN"), "rt-new")

    def test_fails_closed_without_refresh_token(self):
        cfg, _ = _cfg_with_store(OPENAI_CODEX_ACCESS_TOKEN=_jwt(int(time.time()) - 10))
        with self.assertRaises(MissingCredential):
            codex_auth.ensure_fresh(cfg, transport=lambda *a, **k: (200, {}))


class DeviceFlow(unittest.TestCase):
    def test_start_normalizes_response(self):
        def fake(method, url, headers=None, params=None, json_body=None, **kw):
            self.assertTrue(url.endswith("/deviceauth/usercode"))
            self.assertEqual(json_body, {"client_id": codex_auth.CLIENT_ID})
            return 200, {"device_auth_id": "da1", "user_code": "ABCD-1234",
                         "interval": "5", "expires_at": "soon"}
        d = codex_auth.start_device_auth(transport=fake)
        self.assertEqual(d["device_auth_id"], "da1")
        self.assertEqual(d["user_code"], "ABCD-1234")
        self.assertEqual(d["interval"], 5)            # string interval parsed (Codex CLI quirk)
        self.assertEqual(d["verification_url"], codex_auth.VERIFICATION_URL)

    def test_poll_pending_on_403(self):
        def fake(method, url, headers=None, params=None, json_body=None, **kw):
            raise HttpError(403, '{"error":{"code":"deviceauth_authorization_pending"}}')
        self.assertEqual(codex_auth.poll_device_auth("da1", "C", transport=fake), ("pending", None))

    def test_poll_ok_returns_code(self):
        def fake(method, url, headers=None, params=None, json_body=None, **kw):
            self.assertEqual(json_body, {"device_auth_id": "da1", "user_code": "C"})
            return 200, {"authorization_code": "ac", "code_verifier": "cv", "code_challenge": "cc"}
        status, payload = codex_auth.poll_device_auth("da1", "C", transport=fake)
        self.assertEqual(status, "ok")
        self.assertEqual(payload["authorization_code"], "ac")

    def test_poll_raises_on_real_error(self):
        def fake(method, url, headers=None, params=None, json_body=None, **kw):
            raise HttpError(500, "boom")
        with self.assertRaises(HttpError):
            codex_auth.poll_device_auth("da1", "C", transport=fake)

    def test_exchange_persists_tokens(self):
        cfg, store = _cfg_with_store()
        tok = _jwt(int(time.time()) + 3600, "acct-9")
        captured = {}

        def form(url, fields, timeout=30.0):
            captured["url"] = url
            captured["fields"] = fields
            return 200, {"id_token": "id", "access_token": tok, "refresh_token": "rt-x"}

        access, acct = codex_auth.exchange_device_code(
            cfg, {"authorization_code": "ac", "code_verifier": "cv"}, form_transport=form)
        self.assertEqual(acct, "acct-9")
        self.assertEqual(captured["fields"]["grant_type"], "authorization_code")
        self.assertEqual(captured["fields"]["redirect_uri"], codex_auth.DEVICE_REDIRECT_URI)
        self.assertEqual(captured["fields"]["code_verifier"], "cv")
        self.assertEqual(store.get("OPENAI_CODEX_ACCESS_TOKEN"), tok)
        self.assertEqual(store.get("OPENAI_CODEX_REFRESH_TOKEN"), "rt-x")

    def test_exchange_fails_closed_without_code(self):
        cfg, _ = _cfg_with_store()
        with self.assertRaises(MissingCredential):
            codex_auth.exchange_device_code(cfg, {}, form_transport=lambda *a, **k: (200, {}))


if __name__ == "__main__":
    unittest.main()
