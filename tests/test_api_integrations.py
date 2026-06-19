"""API tests for D-27/28/29 — custom-integration CRUD, email test, teams allowlist + webhook.

Uses a temp custom-integration store and a temp SecretStore so nothing touches the real
vault/ or secrets.local.
"""
import tempfile
import unittest
from pathlib import Path

from execution.core.config import get_config
from execution.core.custom_integrations import CustomIntegrationStore
from execution.core.secrets_store import SecretStore
from execution.runtime import build_agent
from execution.web.api import Api
from execution.web.auth import AuthStore, SessionSigner


def _rec(**over):
    base = {"id": "sop_kb", "label": "SOP Provider", "auth_kind": "api_key",
            "fields": [{"label": "API key", "required": True, "secret": True}],
            "base_url": "https://api.sop.example/v1",
            "auth": {"type": "bearer", "field": "SOP_KB_API_KEY"},
            "read_paths": ["/articles"]}
    base.update(over)
    return base


class IntegrationApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "w.db"
        self.agent = build_agent(db_path=db)
        self.auth = AuthStore(db)
        self.auth.ensure_admin("secret")
        self.auth.create_user("bob", "password8", "user", "")
        self.api = Api(self.agent, self.auth, SessionSigner(secret=b"0" * 32))
        # temp custom-integration store
        import execution.core.custom_integrations as cim
        self._old_store = cim._store
        cim._store = CustomIntegrationStore(Path(self.tmp.name) / "integrations.json")
        # temp secret store (so credential writes never touch the real secrets.local)
        self.cfg = get_config()
        self._old_secrets = self.cfg._secrets
        self.cfg._secrets = SecretStore(Path(self.tmp.name) / "secrets.local")

    def tearDown(self):
        import execution.core.custom_integrations as cim
        cim._store = self._old_store
        self.cfg._secrets = self._old_secrets
        self.auth.close()
        self.tmp.cleanup()

    def H(self, method, path, body=None, query=None, user=None):
        return self.api.handle(method, path, query or {}, body or {}, user)

    # ── custom integrations ──
    def test_admin_gated(self):
        self.assertEqual(self.H("POST", "/api/integrations/custom", _rec()).status, 401)
        self.assertEqual(self.H("POST", "/api/integrations/custom", _rec(), user="bob").status, 403)

    def test_create_appears_in_listing_with_fields(self):
        r = self.H("POST", "/api/integrations/custom", _rec(), user="admin")
        self.assertEqual(r.status, 200, r.payload)
        listing = self.H("GET", "/api/integrations", user="bob").payload["integrations"]
        card = next(i for i in listing if i["integration"] == "sop_kb")
        self.assertEqual(card["kind"], "custom")
        self.assertEqual(card["base_url"], "https://api.sop.example/v1")
        self.assertFalse(card["configured"])
        f = self.H("GET", "/api/integrations/sop_kb/fields", user="bob").payload
        self.assertEqual(f["fields"][0]["key"], "SOP_KB_API_KEY")
        self.assertEqual(f["fields"][0]["label"], "API key")

    def test_credentials_roundtrip_and_rename_migration(self):
        self.H("POST", "/api/integrations/custom", _rec(), user="admin")
        r = self.H("POST", "/api/integrations/sop_kb/credentials",
                   {"SOP_KB_API_KEY": "sek-value"}, user="admin")
        self.assertEqual(r.status, 200, r.payload)
        self.assertTrue(r.payload["configured"])
        # rename: id changes, the stored secret value moves with it
        r = self.H("POST", "/api/integrations/custom/sop_kb/rename", {"id": "sop_docs"},
                   user="admin")
        self.assertEqual(r.status, 200, r.payload)
        self.assertEqual(self.cfg._secrets.get("SOP_DOCS_API_KEY"), "sek-value")
        self.assertIsNone(self.cfg._secrets.get("SOP_KB_API_KEY"))

    def test_update_and_delete(self):
        self.H("POST", "/api/integrations/custom", _rec(), user="admin")
        self.H("POST", "/api/integrations/sop_kb/credentials",
               {"SOP_KB_API_KEY": "sek"}, user="admin")
        r = self.H("POST", "/api/integrations/custom/sop_kb",
                   {**_rec(), "read_paths": ["/articles", "/v2/search"]}, user="admin")
        self.assertEqual(r.payload["read_paths"], ["/articles", "/v2/search"])
        r = self.H("DELETE", "/api/integrations/custom/sop_kb", user="admin")
        self.assertTrue(r.payload["ok"])
        self.assertIsNone(self.cfg._secrets.get("SOP_KB_API_KEY"))   # secrets cleared
        self.assertEqual(self.H("GET", "/api/integrations/custom/sop_kb",
                                user="admin").status, 404)

    def test_validation_errors_are_400(self):
        self.assertEqual(self.H("POST", "/api/integrations/custom",
                                _rec(id="kaseya"), user="admin").status, 400)
        self.assertEqual(self.H("POST", "/api/integrations/custom",
                                _rec(base_url="http://insecure"), user="admin").status, 400)

    # ── email ──
    def test_email_test_unconfigured_400(self):
        r = self.H("POST", "/api/integrations/email/test", {}, user="admin")
        self.assertEqual(r.status, 400)

    def test_field_metadata_for_builtins(self):
        f = self.H("GET", "/api/integrations/email/fields", user="bob").payload["fields"]
        by_key = {x["key"]: x for x in f}
        self.assertEqual(by_key["EMAIL_FROM"]["label"], "From address")
        self.assertFalse(by_key["EMAIL_FROM"]["secret"])
        self.assertTrue(by_key["EMAIL_ALLOWED_RECIPIENTS"]["hidden"])  # managed by the panel UI
        self.assertTrue(by_key["EMAIL_API_KEY"]["secret"])
        t = self.H("GET", "/api/integrations/msteams/fields", user="bob").payload["fields"]
        tk = {x["key"]: x for x in t}
        self.assertTrue(tk["TEAMS_ALLOWED_USERS"]["hidden"])      # managed by the panel UI
        self.assertFalse(tk["TEAMS_CLIENT_ID"]["secret"])
        self.assertTrue(tk["TEAMS_CLIENT_SECRET"]["secret"])

    def test_email_card_listed(self):
        listing = self.H("GET", "/api/integrations", user="bob").payload["integrations"]
        self.assertTrue(any(i["integration"] == "email" for i in listing))
        self.assertTrue(any(i["integration"] == "msteams" for i in listing))

    def test_email_recipients_roundtrip(self):
        r = self.H("GET", "/api/integrations/email/recipients", user="admin")
        self.assertEqual(r.status, 200)
        r = self.H("POST", "/api/integrations/email/recipients",
                   {"entries": ["admin@example.com", "@example.com", "admin@example.com"]}, user="admin")
        self.assertEqual(r.status, 200, r.payload)
        self.assertEqual(r.payload["entries"], ["admin@example.com", "@example.com"])  # lowered + deduped
        self.assertFalse(r.payload["allow_all"])
        r = self.H("POST", "/api/integrations/email/recipients",
                   {"entries": ["not-an-address"]}, user="admin")
        self.assertEqual(r.status, 400)
        r = self.H("POST", "/api/integrations/email/recipients",
                   {"entries": [], "allow_all": True}, user="admin")
        self.assertTrue(r.payload["allow_all"])
        self.assertEqual(self.H("POST", "/api/integrations/email/recipients",
                                {"entries": []}, user="bob").status, 403)

    # ── teams ──
    def test_allowlist_roundtrip(self):
        r = self.H("GET", "/api/integrations/msteams/allowlist", user="admin")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.payload["webhook_path"], "/api/teams/messages")
        r = self.H("POST", "/api/integrations/msteams/allowlist",
                   {"entries": [{"id": "aad-123", "name": "Alex", "user": "admin"}]}, user="admin")
        self.assertEqual(r.status, 200, r.payload)
        self.assertEqual(r.payload["entries"],
                         [{"id": "aad-123", "name": "Alex", "user": "admin"}])
        self.assertFalse(r.payload["allow_all"])
        # linking a non-existent dashboard account is rejected
        r = self.H("POST", "/api/integrations/msteams/allowlist",
                   {"entries": [{"id": "aad-9", "name": "X", "user": "ghost"}]}, user="admin")
        self.assertEqual(r.status, 400)

    def test_allowlist_rejects_garbage_ids(self):
        r = self.H("POST", "/api/integrations/msteams/allowlist",
                   {"entries": [{"id": "../../etc", "name": "x"}]}, user="admin")
        self.assertEqual(r.status, 400)

    def test_logo_upload_resizes_to_fit(self):
        import base64
        import io
        import os
        from PIL import Image
        os.environ["MSPAI_BRANDING_DIR"] = str(Path(self.tmp.name) / "brand")
        try:
            buf = io.BytesIO()
            Image.new("RGBA", (1200, 800), (99, 102, 241, 255)).save(buf, format="PNG")
            r = self.H("POST", "/api/branding/logo",
                       {"content_b64": base64.b64encode(buf.getvalue()).decode()}, user="admin")
            self.assertEqual(r.status, 200, r.payload)
            stored = Image.open(Path(self.tmp.name) / "brand" / "logo.png")
            self.assertLessEqual(max(stored.size), 512)               # fits the box
            self.assertAlmostEqual(stored.size[0] / stored.size[1], 1200 / 800, places=2)  # aspect kept
        finally:
            os.environ.pop("MSPAI_BRANDING_DIR", None)

    def test_m365_per_client_device_flow(self):
        import os
        import shutil
        import tempfile
        from unittest import mock
        d = tempfile.mkdtemp()
        (Path(d) / "clients" / "acme").mkdir(parents=True)
        old = os.environ.get("MSPAI_VAULT_PATH")
        os.environ["MSPAI_VAULT_PATH"] = d
        try:
            self.H("POST", "/api/integrations/m365/credentials", {"M365_CLIENT_ID": "app-1"},
                   user="admin")
            # admin-gated
            self.assertEqual(self.H("POST", "/api/integrations/m365/oauth/start",
                                    {"tenant": "acme"}, user="bob").status, 403)
            # unknown client rejected
            self.assertEqual(self.H("POST", "/api/integrations/m365/oauth/start",
                                    {"tenant": "ghost"}, user="admin").status, 400)
            # start for acme (Microsoft endpoint stubbed)
            with mock.patch("execution.core.m365_auth._form_post",
                            return_value=(200, {"device_code": "D", "user_code": "ABCD-EFGH",
                                                "verification_uri": "https://microsoft.com/devicelogin",
                                                "interval": 5, "expires_in": 900})):
                r = self.H("POST", "/api/integrations/m365/oauth/start", {"tenant": "acme"},
                           user="admin")
            self.assertEqual(r.status, 200, r.payload)
            self.assertEqual(r.payload["user_code"], "ABCD-EFGH")
            self.assertEqual(r.payload["tenant"], "acme")
            self.assertNotIn("device_code", r.payload)
            # per-client status list
            cl = self.H("GET", "/api/integrations/m365/clients", user="admin").payload
            self.assertTrue(cl["app_configured"])
            self.assertFalse(next(c for c in cl["clients"] if c["tenant"] == "acme")["connected"])
            self.assertEqual(self.H("POST", "/api/integrations/m365/oauth/poll", {},
                                    user="admin").status, 400)
        finally:
            os.environ.pop("MSPAI_VAULT_PATH", None) if old is None else \
                os.environ.__setitem__("MSPAI_VAULT_PATH", old)
            shutil.rmtree(d, ignore_errors=True)

    def test_m365_card_listed_and_app_id_typed(self):
        listing = self.H("GET", "/api/integrations", user="bob").payload["integrations"]
        self.assertTrue(any(i["integration"] == "m365" for i in listing))
        f = {x["key"]: x for x in self.H("GET", "/api/integrations/m365/fields", user="bob").payload["fields"]}
        self.assertFalse(f["M365_CLIENT_ID"]["secret"])      # typed, visible
        self.assertNotIn("M365_REFRESH_TOKEN", f)            # tokens are per-client, not global keys

    def test_webhook_unconfigured_is_404(self):
        # no TEAMS_* creds in the temp store → bridge fails closed before any JWT work
        r = self.api.teams_webhook("Bearer whatever", {"type": "message"})
        self.assertEqual(r.status, 404)


if __name__ == "__main__":
    unittest.main()
