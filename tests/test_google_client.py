"""GoogleClient host routing + path guard + probe, and the gws scope allowlist (D-118). No network."""
import tempfile
import unittest
from pathlib import Path

from execution.clients.google import GoogleClient, build_gws
from execution.clients.scopes import is_allowed_read
from execution.core.config import Config
from execution.core.credentials import MissingCredential
from execution.core.secrets_store import SecretStore


def _capture(store):
    def t(method, url, headers=None, params=None, json_body=None, **kw):
        store["method"] = method
        store["url"] = url
        store["auth"] = (headers or {}).get("Authorization")
        store["body"] = json_body
        return 200, {"users": []}
    return t


class HostRouting(unittest.TestCase):
    def test_leading_segment_selects_the_right_googleapis_host(self):
        cap = {}
        gc = GoogleClient(lambda: "tok", transport=_capture(cap))
        cases = {
            "/admin/directory/v1/users": "https://admin.googleapis.com/admin/directory/v1/users",
            "/drive/v3/drives": "https://www.googleapis.com/drive/v3/drives",
            "/gmail/v1/users/me/settings/forwarding": "https://gmail.googleapis.com/gmail/v1/users/me/settings/forwarding",
            "/apps/licensing/v1/product/x/sku/y/user/z": "https://licensing.googleapis.com/apps/licensing/v1/product/x/sku/y/user/z",
        }
        for path, expect in cases.items():
            gc.get(path)
            self.assertEqual(cap["url"], expect)
        self.assertEqual(cap["auth"], "Bearer tok")

    def test_bad_path_never_calls_transport(self):
        cap = {}
        gc = GoogleClient(lambda: "tok", transport=_capture(cap))
        for bad in ("admin/x", "/admin/../etc", "https://evil/x", "//evil/x"):
            out = gc.get(bad)
            self.assertIn("error", out)
        self.assertEqual(cap, {})                     # transport was never invoked

    def test_probe_reads_customer_record(self):
        def t(method, url, headers=None, params=None, json_body=None, **kw):
            self.assertTrue(url.endswith("/admin/directory/v1/customers/my_customer"))
            return 200, {"customerDomain": "acme.com"}
        p = GoogleClient(lambda: "tok", transport=t).probe()
        self.assertTrue(p["ok"])
        self.assertIn("acme.com", p["detail"])


class Scopes(unittest.TestCase):
    def test_read_allowlist_is_directory_only(self):
        for ok_path in ("/admin/directory/v1/users",
                        "/admin/directory/v1/users/a@b.com",
                        "/admin/directory/v1/groups",
                        "/admin/directory/v1/groups/g@b.com/members",
                        "/admin/directory/v1/customers/my_customer",
                        "/admin/directory/v1/customer/my_customer/orgunits"):
            self.assertTrue(is_allowed_read("gws", ok_path)[0], ok_path)
        # not (yet) allowlisted → fail closed
        self.assertFalse(is_allowed_read("gws", "/admin/directory/v1/domains")[0])
        self.assertFalse(is_allowed_read("gws", "/drive/v3/drives")[0])
        # host-escape attempts fail closed
        self.assertFalse(is_allowed_read("gws", "/admin/directory/v1/../../x")[0])
        # write/delete allowlists are covered in tests/test_gws_writes.py


class BuildGws(unittest.TestCase):
    def _cfg(self, tmp):
        d = Path(tmp)
        env = d / ".env"
        env.write_text(f"MSPAI_VAULT_PATH={d / 'vault'}\n", encoding="utf-8")
        env.chmod(0o600)
        return Config(env_path=env, secret_store=SecretStore(path=d / "secrets.local"))

    def test_fail_closed_when_unbound_or_not_signed_in(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self._cfg(td)
            self.assertRaises(MissingCredential, build_gws, cfg, "*")
            self.assertRaises(MissingCredential, build_gws, cfg, "acme")   # not signed in


if __name__ == "__main__":
    unittest.main()
