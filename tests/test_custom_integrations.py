"""Custom-integration tests (D-27) — the store validates + the boundaries hold.

Proves: metadata-only storage (no secrets), id/field validation, rename migrates keys,
spec_for() resolution, scoped_read honors the owner's read allowlist (empty = fail closed),
and the generic client injects auth without ever exposing values.
"""
import json
import tempfile
import unittest
from pathlib import Path

from execution.core.custom_integrations import (
    CustomIntegrationStore, field_key, RESERVED_IDS)


def _rec(**over):
    base = {
        "id": "sop_kb", "label": "SOP Provider", "auth_kind": "api_key",
        "fields": [{"label": "API key", "required": True, "secret": True}],
        "base_url": "https://api.sop.example/v1",
        "auth": {"type": "bearer", "field": "SOP_KB_API_KEY"},
        "read_paths": ["/articles", "/search"],
    }
    base.update(over)
    return base


class StoreValidation(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = CustomIntegrationStore(Path(self.dir.name) / "integrations.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_create_and_get(self):
        ci = self.store.create(_rec())
        self.assertEqual(ci.id, "sop_kb")
        self.assertEqual(ci.fields[0]["key"], "SOP_KB_API_KEY")
        self.assertEqual(self.store.get("sop_kb").label, "SOP Provider")

    def test_no_secrets_in_file(self):
        self.store.create(_rec())
        raw = (Path(self.dir.name) / "integrations.json").read_text()
        data = json.loads(raw)
        # only metadata keys — no value-bearing fields exist in the schema at all
        self.assertNotIn("value", raw.lower())
        self.assertEqual(set(data), {"sop_kb"})

    def test_rejects_bad_ids(self):
        for bad in ("", "Sop-KB", "1abc", "a" * 50, "kaseya", "email", "msteams", "custom"):
            with self.assertRaises(ValueError):
                self.store.create(_rec(id=bad))

    def test_reserved_ids_cover_builtins(self):
        for name in ("kaseya", "cylance", "huntress", "anthropic", "openai", "email", "msteams"):
            self.assertIn(name, RESERVED_IDS)

    def test_requires_https_base(self):
        with self.assertRaises(ValueError):
            self.store.create(_rec(base_url="http://api.sop.example"))

    def test_auth_field_must_reference_known_key(self):
        with self.assertRaises(ValueError):
            self.store.create(_rec(auth={"type": "bearer", "field": "OTHER_KEY"}))

    def test_read_paths_validated(self):
        with self.assertRaises(ValueError):
            self.store.create(_rec(read_paths=["https://evil.com/x"]))
        with self.assertRaises(ValueError):
            self.store.create(_rec(read_paths=["/ok/../../etc"]))

    def test_duplicate_create_rejected(self):
        self.store.create(_rec())
        with self.assertRaises(ValueError):
            self.store.create(_rec())

    def test_rename_migrates_keys(self):
        self.store.create(_rec())
        ci, key_map = self.store.rename("sop_kb", "sop_docs")
        self.assertEqual(ci.id, "sop_docs")
        self.assertEqual(key_map, {"SOP_KB_API_KEY": "SOP_DOCS_API_KEY"})
        self.assertEqual(ci.auth["field"], "SOP_DOCS_API_KEY")
        self.assertIsNone(self.store.get("sop_kb"))

    def test_delete(self):
        self.store.create(_rec())
        self.store.delete("sop_kb")
        self.assertIsNone(self.store.get("sop_kb"))
        with self.assertRaises(ValueError):
            self.store.delete("sop_kb")

    def test_field_key_derivation(self):
        self.assertEqual(field_key("sop_kb", "API key"), "SOP_KB_API_KEY")
        self.assertEqual(field_key("x", "User name!"), "X_USER_NAME")


class SpecResolution(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = CustomIntegrationStore(Path(self.dir.name) / "integrations.json")
        self.store.create(_rec())
        import execution.core.custom_integrations as m
        self._old = m._store
        m._store = self.store

    def tearDown(self):
        import execution.core.custom_integrations as m
        m._store = self._old
        self.dir.cleanup()

    def test_spec_for_resolves_custom(self):
        from execution.core import credentials
        spec = credentials.spec_for("sop_kb")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.group, "custom")
        self.assertEqual(spec.required, ("SOP_KB_API_KEY",))

    def test_spec_for_unknown_is_none(self):
        from execution.core import credentials
        self.assertIsNone(credentials.spec_for("nope"))

    def test_builtin_specs_win(self):
        from execution.core import credentials
        self.assertEqual(credentials.spec_for("kaseya").group, "vendor")

    def test_scoped_read_honors_custom_allowlist(self):
        from execution.clients.scopes import is_allowed_read
        self.assertTrue(is_allowed_read("sop_kb", "/articles")[0])
        self.assertTrue(is_allowed_read("sop_kb", "/articles/42")[0])
        self.assertFalse(is_allowed_read("sop_kb", "/admin/delete")[0])
        self.assertFalse(is_allowed_read("sop_kb", "/articles_evil")[0])

    def test_scoped_read_empty_allowlist_fails_closed(self):
        self.store.create(_rec(id="locked", read_paths=[],
                               auth={"type": "bearer", "field": "LOCKED_API_KEY"}))
        from execution.clients.scopes import is_allowed_read
        ok, reason = is_allowed_read("locked", "/anything")
        self.assertFalse(ok)
        self.assertIn("no readable paths", reason)


class GenericClient(unittest.TestCase):
    def _client(self, auth, env, **kw):
        from execution.clients.custom import CustomHTTPClient
        calls = []
        def transport(method, url, headers=None, params=None, json_body=None, **_):
            calls.append({"method": method, "url": url, "headers": headers or {},
                          "params": params or {}})
            return 200, {"ok": True}
        c = CustomHTTPClient("sop_kb", "https://api.sop.example/v1", auth, env,
                             transport=transport, **kw)
        return c, calls

    def test_bearer_auth_header(self):
        c, calls = self._client({"type": "bearer", "field": "SOP_KB_API_KEY"},
                                {"SOP_KB_API_KEY": "sek"})
        c.get("/articles")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer sek")
        self.assertEqual(calls[0]["url"], "https://api.sop.example/v1/articles")

    def test_basic_auth(self):
        c, calls = self._client(
            {"type": "basic", "user_field": "U", "pass_field": "P"}, {"U": "u", "P": "p"})
        c.get("/x")
        self.assertTrue(calls[0]["headers"]["Authorization"].startswith("Basic "))

    def test_header_and_query_auth(self):
        c, calls = self._client({"type": "header", "name": "X-Key", "field": "K"}, {"K": "v"})
        c.get("/x")
        self.assertEqual(calls[0]["headers"]["X-Key"], "v")
        c2, calls2 = self._client({"type": "query", "name": "apikey", "field": "K"}, {"K": "v"})
        c2.get("/x")
        self.assertEqual(calls2[0]["params"]["apikey"], "v")

    def test_path_escape_blocked(self):
        c, calls = self._client({"type": "none"}, {})
        r = c.get("https://evil.com/x")
        self.assertIn("error", r)
        self.assertEqual(calls, [])         # transport never called

    def test_verify_tls_flows_to_transport(self):
        from execution.clients.custom import CustomHTTPClient
        seen = {}
        def transport(method, url, headers=None, params=None, verify_tls=True, **_):
            seen["verify_tls"] = verify_tls
            return 200, {}
        # default verifies; self-signed integrations skip verification
        CustomHTTPClient("x", "https://h/v1", {"type": "none"}, {}, transport=transport).get("/a")
        self.assertTrue(seen["verify_tls"])
        CustomHTTPClient("x", "https://h/v1", {"type": "none"}, {}, verify_tls=False,
                         transport=transport).get("/a")
        self.assertFalse(seen["verify_tls"])

    def test_store_roundtrips_verify_tls(self):
        store = CustomIntegrationStore(Path(tempfile.mkdtemp()) / "i.json")
        store.create(_rec(id="unifi", base_url="https://192.168.1.1/proxy/network/integration",
                          auth={"type": "header", "name": "X-API-Key", "field": "UNIFI_API_KEY"},
                          verify_tls=False, read_paths=["/v1"]))
        self.assertFalse(store.get("unifi").verify_tls)

    def test_probe_surfaces_api_error_message(self):
        from execution.clients._http import HttpError
        from execution.clients.custom import CustomHTTPClient
        def transport(method, url, headers=None, params=None, **_):
            raise HttpError(401, '{"code":"unauthorized","message":"unauthorized"}')
        c = CustomHTTPClient("x", "https://api.x/v1", {"type": "header", "name": "K", "field": "F"},
                             {"F": "bad"}, probe_path="/hosts", transport=transport)
        r = c.probe()
        self.assertFalse(r["ok"])
        self.assertIn("401", r["detail"])
        self.assertIn("unauthorized", r["detail"])
        self.assertIn("rejected", r["detail"])      # auth hint

    def test_probe_uses_probe_path_then_first_read_path(self):
        c, calls = self._client({"type": "none"}, {}, probe_path="/health")
        self.assertTrue(c.probe()["ok"])
        self.assertTrue(calls[0]["url"].endswith("/health"))
        c2, _ = self._client({"type": "none"}, {})
        self.assertFalse(c2.probe()["ok"])  # nothing configured → can't probe


if __name__ == "__main__":
    unittest.main()
