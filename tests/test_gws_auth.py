"""Google Workspace per-client OAuth (D-118) — auth-code sign-in + token refresh, no network.

Runs against the locked-vault inline-sidecar fallback (no vault unlock needed): save/load/clear and
the refresh lifecycle all exercise the plain per-client sidecar under a temp MSPAI_VAULT_PATH.
"""
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path

from execution.core import gws_auth
from execution.core.config import Config
from execution.core.credentials import MissingCredential
from execution.core.secrets_store import SecretStore


def _idtoken(email: str, hd: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"email": email, "hd": hd}).encode()).rstrip(b"=").decode()
    return f"h.{body}.s"


def _cfg(tmp: str, *, configured: bool = True) -> Config:
    d = Path(tmp)
    lines = [f"MSPAI_VAULT_PATH={d / 'vault'}"]
    if configured:
        lines += ["GWS_CLIENT_ID=cid", "GWS_CLIENT_SECRET=secret",
                  "GWS_REDIRECT_URI=https://dash.example/api/gws/oauth/callback"]
    env = d / ".env"
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env.chmod(0o600)
    return Config(env_path=env, secret_store=SecretStore(path=d / "secrets.local"))


class AuthUrl(unittest.TestCase):
    def test_start_auth_builds_offline_consent_url_with_state(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _cfg(td)
            r = gws_auth.start_auth(cfg, "acme", login_hint="admin@acme.com", hosted_domain="acme.com")
            url = r["auth_url"]
            self.assertTrue(url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
            for frag in ("access_type=offline", "prompt=consent", "client_id=cid",
                         f"state={r['state']}", "login_hint=admin", "hd=acme.com"):
                self.assertIn(frag, url)

    def test_start_auth_rejects_all_clients_and_unconfigured(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertRaises(MissingCredential, gws_auth.start_auth, _cfg(td), "*")
            self.assertRaises(MissingCredential, gws_auth.start_auth,
                              _cfg(td, configured=False), "acme")


class CompleteAuth(unittest.TestCase):
    def test_exchange_saves_tokens_and_connects(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _cfg(td)
            r = gws_auth.start_auth(cfg, "acme")
            seen = {}

            def fake(url, fields, timeout=30):
                seen.update(fields)
                return 200, {"access_token": "at1", "refresh_token": "rt1", "expires_in": 3599,
                             "id_token": _idtoken("admin@acme.com", "acme.com")}

            status, tenant = gws_auth.complete_auth(cfg, r["state"], "the-code", transport=fake)
            self.assertEqual((status, tenant), ("connected", "acme"))
            self.assertEqual(seen["grant_type"], "authorization_code")
            self.assertEqual(seen["code"], "the-code")
            self.assertEqual(seen["client_secret"], "secret")
            self.assertTrue(gws_auth.is_connected(cfg, "acme"))
            self.assertEqual(gws_auth.list_connected(cfg), ["acme"])
            h = gws_auth.health(cfg, "acme")
            self.assertTrue(h["connected"])
            self.assertEqual(h["admin_email"], "admin@acme.com")
            self.assertEqual(h["domain"], "acme.com")

    def test_missing_refresh_token_is_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _cfg(td)
            r = gws_auth.start_auth(cfg, "acme")
            status, msg = gws_auth.complete_auth(
                cfg, r["state"], "c", transport=lambda *a, **k: (200, {"access_token": "at"}))
            self.assertEqual(status, "error")
            self.assertIn("refresh token", msg)
            self.assertFalse(gws_auth.is_connected(cfg, "acme"))

    def test_bad_state_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            status, msg = gws_auth.complete_auth(_cfg(td), "bogus-state", "code",
                                                 transport=lambda *a, **k: self.fail("no exchange"))
            self.assertEqual(status, "error")


class EnsureFresh(unittest.TestCase):
    def _connect(self, cfg):
        r = gws_auth.start_auth(cfg, "acme")
        gws_auth.complete_auth(cfg, r["state"], "c", transport=lambda *a, **k: (
            200, {"access_token": "at1", "refresh_token": "rt1", "expires_in": 3599,
                  "id_token": _idtoken("a@acme.com", "acme.com")}))

    def test_valid_token_returned_without_refresh(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _cfg(td)
            self._connect(cfg)
            tok = gws_auth.ensure_fresh(cfg, "acme", transport=lambda *a, **k: self.fail("no refresh"))
            self.assertEqual(tok, "at1")

    def test_expired_token_refreshes_and_persists(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _cfg(td)
            self._connect(cfg)
            # force expiry into the past, then refresh
            gws_auth.save_tokens(cfg, "acme", {"refresh_token": "rt1", "access_token": "old",
                                               "access_expires": int(time.time()) - 10})
            seen = {}

            def fake(url, fields, timeout=30):
                seen.update(fields)
                return 200, {"access_token": "at2", "expires_in": 3600}   # Google reuses the rt

            tok = gws_auth.ensure_fresh(cfg, "acme", transport=fake)
            self.assertEqual(tok, "at2")
            self.assertEqual(seen["grant_type"], "refresh_token")
            self.assertEqual(seen["client_secret"], "secret")
            # persisted: next call returns the cached new token with no refresh
            self.assertEqual(gws_auth.ensure_fresh(cfg, "acme",
                                                   transport=lambda *a, **k: self.fail("cached")), "at2")

    def test_not_signed_in_raises(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertRaises(MissingCredential, gws_auth.ensure_fresh, _cfg(td), "nobody")


class Disconnect(unittest.TestCase):
    def test_clear_tokens_disconnects(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _cfg(td)
            r = gws_auth.start_auth(cfg, "acme")
            gws_auth.complete_auth(cfg, r["state"], "c", transport=lambda *a, **k: (
                200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600,
                      "id_token": _idtoken("a@acme.com", "acme.com")}))
            self.assertTrue(gws_auth.is_connected(cfg, "acme"))
            self.assertTrue(gws_auth.clear_tokens(cfg, "acme"))
            self.assertFalse(gws_auth.is_connected(cfg, "acme"))
            self.assertEqual(gws_auth.list_connected(cfg), [])


if __name__ == "__main__":
    unittest.main()
