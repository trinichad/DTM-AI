"""Microsoft 365 / Graph PER-CLIENT delegated device-code auth tests (D-32/D-33)."""
import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from execution.core import m365_auth
from execution.core.credentials import MissingCredential


def _jwt(exp: int, tid: str = "tenant-guid") -> str:
    def b(d): return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{b({'alg':'none'})}.{b({'exp':exp,'tid':tid})}.sig"


class StubCfg:
    def __init__(self, d, root): self.d = d; self.d["MSPAI_VAULT_PATH"] = str(root)
    def get(self, k, default=None): return self.d.get(k, default)
    def present(self, k): return bool(self.d.get(k))
    def int(self, k, default=0):
        try: return int(self.d.get(k, default))
        except (TypeError, ValueError): return default


class PerClientStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = StubCfg({"M365_CLIENT_ID": "app-1"}, self.tmp.name)
        m365_auth._flows.clear()
        self._orig = m365_auth._form_post

    def tearDown(self):
        m365_auth._form_post = self._orig
        m365_auth._flows.clear()
        self.tmp.cleanup()

    def _mk_clients(self, *names):
        for n in names:
            (Path(self.tmp.name) / "clients" / n).mkdir(parents=True, exist_ok=True)

    def test_tokens_stored_per_client_and_isolated(self):
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "r-acme", "tenant_id": "t1"})
        m365_auth.save_tokens(self.cfg, "globex", {"refresh_token": "r-globex", "tenant_id": "t2"})
        self.assertTrue(m365_auth.is_connected(self.cfg, "acme"))
        self.assertTrue(m365_auth.is_connected(self.cfg, "globex"))
        self.assertFalse(m365_auth.is_connected(self.cfg, "other"))
        self.assertEqual(m365_auth.load_tokens(self.cfg, "acme")["refresh_token"], "r-acme")
        self.assertEqual(sorted(m365_auth.list_connected(self.cfg)), ["acme", "globex"])
        # the per-client file is 0600
        p = Path(self.tmp.name) / "clients" / "acme" / "m365.json"
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_token_file_does_not_contain_other_clients(self):
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "SECRET-ACME"})
        m365_auth.save_tokens(self.cfg, "globex", {"refresh_token": "SECRET-GLOBEX"})
        acme_blob = (Path(self.tmp.name) / "clients" / "acme" / "m365.json").read_text()
        self.assertIn("SECRET-ACME", acme_blob)
        self.assertNotIn("SECRET-GLOBEX", acme_blob)

    def test_clear(self):
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "r"})
        self.assertTrue(m365_auth.clear_tokens(self.cfg, "acme"))
        self.assertFalse(m365_auth.is_connected(self.cfg, "acme"))

    # ── device flow ──
    def test_start_uses_builtin_app_when_no_client_id(self):
        # No M365_CLIENT_ID set → falls back to Microsoft's built-in public client (no registration)
        m365_auth._form_post = lambda url, fields, timeout=30: (
            200, {"device_code": "D", "user_code": "U", "interval": 5, "expires_in": 900}) \
            if fields.get("client_id") == m365_auth.MS_GRAPH_CLI_CLIENT_ID \
            else self.fail(f"expected built-in client id, got {fields.get('client_id')}")
        flow = m365_auth.start_device_auth(StubCfg({}, self.tmp.name), "acme")
        self.assertEqual(flow["tenant"], "acme")

    def test_start_requires_specific_tenant(self):
        with self.assertRaises(MissingCredential):
            m365_auth.start_device_auth(self.cfg, "*")                        # not a specific client

    def test_full_flow_persists_under_the_right_client(self):
        m365_auth._form_post = lambda url, fields, timeout=30: (200, {
            "device_code": "D", "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin",
            "interval": 5, "expires_in": 900})
        flow = m365_auth.start_device_auth(self.cfg, "acme")
        self.assertEqual(flow["tenant"], "acme")
        self.assertNotIn("device_code", flow)
        m365_auth._form_post = lambda url, fields, timeout=30: (400, {"error": "authorization_pending"})
        self.assertEqual(m365_auth.poll_device_auth(flow["flow_id"], self.cfg), ("pending", None))
        m365_auth._form_post = lambda url, fields, timeout=30: (200, {
            "access_token": _jwt(int(time.time()) + 3600, tid="acme-tid"),
            "refresh_token": "REFRESH-ACME"})
        status, tenant = m365_auth.poll_device_auth(flow["flow_id"], self.cfg)
        self.assertEqual((status, tenant), ("connected", "acme"))
        toks = m365_auth.load_tokens(self.cfg, "acme")
        self.assertEqual(toks["refresh_token"], "REFRESH-ACME")
        self.assertEqual(toks["tenant_id"], "acme-tid")        # tid captured from the token
        self.assertFalse(m365_auth.is_connected(self.cfg, "globex"))  # only acme got it

    # ── refresh per client ──
    def test_ensure_fresh_refreshes_only_that_client(self):
        m365_auth.save_tokens(self.cfg, "acme", {
            "access_token": _jwt(int(time.time()) - 10), "refresh_token": "old", "tenant_id": "t"})
        new = _jwt(int(time.time()) + 3600)
        m365_auth._form_post = lambda url, fields, timeout=30: (200, {
            "access_token": new, "refresh_token": "rotated"})
        self.assertEqual(m365_auth.ensure_fresh(self.cfg, "acme"), new)
        self.assertEqual(m365_auth.load_tokens(self.cfg, "acme")["refresh_token"], "rotated")

    def test_ensure_fresh_unconnected_client_fails_closed(self):
        with self.assertRaises(MissingCredential):
            m365_auth.ensure_fresh(self.cfg, "never-signed-in")

    def test_valid_cached_token_not_refreshed(self):
        tok = _jwt(int(time.time()) + 3600)
        m365_auth.save_tokens(self.cfg, "acme", {"access_token": tok, "refresh_token": "r"})
        m365_auth._form_post = lambda *a, **k: self.fail("should not refresh a valid token")
        self.assertEqual(m365_auth.ensure_fresh(self.cfg, "acme"), tok)

    # ── health + auto-renew (D-35) ──
    def test_refresh_stamps_last_refresh_and_clears_error(self):
        m365_auth.save_tokens(self.cfg, "acme", {
            "access_token": _jwt(int(time.time()) - 10), "refresh_token": "old",
            "last_error": "stale", "obtained": 100})
        m365_auth._form_post = lambda url, fields, timeout=30: (200, {
            "access_token": _jwt(int(time.time()) + 3600), "refresh_token": "rot"})
        m365_auth.ensure_fresh(self.cfg, "acme")
        h = m365_auth.health(self.cfg, "acme")
        self.assertTrue(h["healthy"])
        self.assertIsNone(h["last_error"])
        self.assertGreater(h["last_refresh"], 0)
        self.assertEqual(h["obtained"], 100)               # preserved across refresh
        self.assertGreater(h["refresh_valid_until"], time.time())

    def test_failed_refresh_records_error_keeps_token(self):
        m365_auth.save_tokens(self.cfg, "acme", {
            "access_token": _jwt(int(time.time()) - 10), "refresh_token": "old"})
        m365_auth._form_post = lambda url, fields, timeout=30: (400, {
            "error": "invalid_grant", "error_description": "token revoked"})
        with self.assertRaises(MissingCredential):
            m365_auth.ensure_fresh(self.cfg, "acme")
        h = m365_auth.health(self.cfg, "acme")
        self.assertFalse(h["healthy"])
        self.assertIn("revoked", h["last_error"])
        self.assertTrue(m365_auth.is_connected(self.cfg, "acme"))   # token kept for re-auth

    def test_renew_all_keepalive(self):
        m365_auth.save_tokens(self.cfg, "acme", {
            "access_token": _jwt(int(time.time()) + 3600), "refresh_token": "a"})
        m365_auth.save_tokens(self.cfg, "globex", {
            "access_token": _jwt(int(time.time()) + 3600), "refresh_token": "b"})
        # renew() drops the cached access token and forces a real refresh-grant
        m365_auth._form_post = lambda url, fields, timeout=30: (200, {
            "access_token": _jwt(int(time.time()) + 3600), "refresh_token": "rotated"})
        r = m365_auth.renew_all(self.cfg)
        self.assertEqual(sorted(r["ok"]), ["acme", "globex"])
        self.assertEqual(r["failed"], [])
        self.assertEqual(m365_auth.load_tokens(self.cfg, "acme")["refresh_token"], "rotated")

    def test_health_unconnected(self):
        self.assertEqual(m365_auth.health(self.cfg, "nobody"), {"connected": False})


class CredVaultBackedStore(unittest.TestCase):
    """D-37 — secrets live in the client's CredVault entry; the sidecar holds status only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = StubCfg({"M365_CLIENT_ID": "app-1"}, self.tmp.name)
        from execution.core.credvault import get_credvault
        self.vault = get_credvault(self.cfg)
        self.vault.set_passphrase("passphrase-1", "admin")
        self._orig = m365_auth._form_post

    def tearDown(self):
        m365_auth._form_post = self._orig
        self.tmp.cleanup()

    def _side_path(self, tenant="acme"):
        return Path(self.tmp.name) / "clients" / tenant / "m365.json"

    def test_secrets_in_vault_not_in_sidecar(self):
        m365_auth.save_tokens(self.cfg, "acme", {
            "refresh_token": "SECRET-R", "access_token": _jwt(int(time.time()) + 3600),
            "tenant_id": "t1", "obtained": 100})
        raw = self._side_path().read_text()
        self.assertNotIn("SECRET-R", raw)
        side = json.loads(raw)
        self.assertTrue(side["connected"])
        self.assertTrue(side["refresh_fp"])
        self.assertGreater(side["access_expires"], time.time())
        self.assertIn("m365_oauth", [c["label"] for c in self.vault.admin_list("acme")])
        self.assertTrue(m365_auth.is_connected(self.cfg, "acme"))
        self.assertEqual(m365_auth.load_tokens(self.cfg, "acme")["refresh_token"], "SECRET-R")

    def test_legacy_file_migrates_on_first_use(self):
        p = self._side_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"refresh_token": "OLD-R", "tenant_id": "t", "obtained": 5}))
        toks = m365_auth.load_tokens(self.cfg, "acme")
        self.assertEqual(toks["refresh_token"], "OLD-R")
        self.assertNotIn("OLD-R", p.read_text())             # moved out of the plain file
        self.assertEqual(json.loads(p.read_text())["obtained"], 5)
        self.assertIn("m365_oauth", [c["label"] for c in self.vault.admin_list("acme")])

    def test_locked_vault_fails_closed_without_faking_token_failure(self):
        from execution.core.credvault import VaultLocked
        m365_auth.save_tokens(self.cfg, "acme", {
            "refresh_token": "r", "access_token": _jwt(int(time.time()) - 10)})
        self.vault.lock()
        m365_auth._form_post = lambda *a, **k: self.fail("must not reach Microsoft while locked")
        with self.assertRaises(VaultLocked):
            m365_auth.ensure_fresh(self.cfg, "acme")
        self.assertTrue(m365_auth.is_connected(self.cfg, "acme"))   # sidecar, no decrypt needed
        h = m365_auth.health(self.cfg, "acme")
        self.assertTrue(h["connected"])
        self.assertTrue(h["healthy"])                       # a locked vault is NOT a token failure

    def test_renew_all_reports_locked_not_failed(self):
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "r", "access_token": ""})
        self.vault.lock()
        r = m365_auth.renew_all(self.cfg)
        self.assertEqual(r["locked"], ["acme"])
        self.assertEqual(r["failed"], [])

    def test_disconnect_requires_unlock_then_deletes_everywhere(self):
        from execution.core.credvault import VaultLocked
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "r", "access_token": ""})
        self.vault.lock()
        with self.assertRaises(VaultLocked):
            m365_auth.clear_tokens(self.cfg, "acme")        # must not leave the secret behind
        self.vault.unlock("passphrase-1", "admin")
        self.assertTrue(m365_auth.clear_tokens(self.cfg, "acme"))
        self.assertFalse(m365_auth.is_connected(self.cfg, "acme"))
        self.assertEqual([c["label"] for c in self.vault.admin_list("acme")], [])

    def test_deleting_vault_entry_self_heals_to_disconnected(self):
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "r", "access_token": ""})
        self.vault.delete("acme", "m365_oauth")             # owner deletes it in the Memory tab
        self.assertFalse(m365_auth.load_tokens(self.cfg, "acme").get("refresh_token"))
        self.assertFalse(m365_auth.is_connected(self.cfg, "acme"))

    def test_unlock_sweep_migrates_all_services(self):
        # tokens connected while the vault was locked (inline fallback) move into the vault the
        # moment it's unlocked — across BOTH services (the bug the owner hit with EXO, D-41)
        self.vault.lock()
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "g", "access_token": ""})
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "x", "access_token": ""},
                              service="exo")
        self.assertIn("g", self._side_path("acme").read_text())          # inline while locked
        self.vault.unlock("passphrase-1", "admin")
        swept = m365_auth.migrate_inline_secrets(self.cfg)
        self.assertEqual(sorted(swept["moved"]), ["acme/exo_oauth", "acme/m365_oauth"])
        self.assertNotIn("\"refresh_token\"", self._side_path("acme").read_text())
        exo_side = (Path(self.tmp.name) / "clients" / "acme" / "exo.json").read_text()
        self.assertNotIn("refresh_token", exo_side)                      # EXO secret now in vault
        labels = [c["label"] for c in self.vault.admin_list("acme")]
        self.assertIn("m365_oauth", labels)
        self.assertIn("exo_oauth", labels)

    def test_rotation_while_locked_falls_back_inline_then_migrates(self):
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "r1", "access_token": ""})
        self.vault.lock()
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "r2", "access_token": "",
                                                 "tenant_id": "t"})
        self.assertIn("r2", self._side_path().read_text())  # rotation never lost (inline fallback)
        self.vault.unlock("passphrase-1", "admin")
        self.assertEqual(m365_auth.load_tokens(self.cfg, "acme")["refresh_token"], "r2")
        self.assertNotIn("r2", self._side_path().read_text())   # migrated on first unlocked use


class GraphClientPerTenant(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = StubCfg({"M365_CLIENT_ID": "app-1"}, self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_fails_closed_for_star_and_unconnected(self):
        from execution.clients.m365 import build_m365
        with self.assertRaises(MissingCredential):
            build_m365(self.cfg, "*")
        with self.assertRaises(MissingCredential):
            build_m365(self.cfg, "acme")             # not signed in

    def test_build_ok_after_signin(self):
        m365_auth.save_tokens(self.cfg, "acme", {
            "access_token": _jwt(int(time.time()) + 3600), "refresh_token": "r"})
        from execution.clients.m365 import build_m365
        c = build_m365(self.cfg, "acme")
        self.assertIsNotNone(c)

    def test_get_sends_bearer(self):
        from execution.clients.m365 import M365Client
        calls = []
        c = M365Client(lambda: "TOK",
                       transport=lambda m, u, headers=None, params=None, **_:
                       calls.append((u, headers)) or (200, {"value": []}))
        c.get("/users")
        self.assertEqual(calls[0][1]["Authorization"], "Bearer TOK")


class ListUsersSkill(unittest.TestCase):
    def test_returns_slimmed_users_for_bound_client(self):
        from execution.core.context import ToolContext
        from execution.skills import m365_list_users

        class FakeGraph:
            def get(self, path, params=None):
                self.path, self.params = path, params
                return {"value": [{"id": "1", "displayName": "Alex"}]}
        fake = FakeGraph()
        # client_factory is called with the bound tenant — proves per-client routing
        seen = {}
        def factory(integration, tenant): seen["t"] = tenant; return fake
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=factory)
        r = m365_list_users.run(ctx, top=50)
        self.assertEqual(r["count"], 1)
        self.assertEqual(seen["t"], "acme")          # used acme's connection
        self.assertEqual(fake.path, "/users")

    def test_all_clients_aggregates_across_connected(self):
        # D-51: tenant '*' iterates every signed-in M365 client and tags each user's tenant.
        import tempfile
        from execution.core.context import ToolContext
        from execution.core import m365_auth
        from execution.skills import m365_list_users
        tmp = tempfile.mkdtemp()
        cfg = StubCfg({}, tmp)
        m365_auth.save_tokens(cfg, "acme", {"refresh_token": "r", "access_token": ""})
        m365_auth.save_tokens(cfg, "globex", {"refresh_token": "r", "access_token": ""})

        class FakeGraph:
            def __init__(self, t): self.t = t
            def get(self, path, params=None):
                return {"value": [{"id": "1", "displayName": f"{self.t}-user"}]}
        seen = []
        def factory(integration, tenant): seen.append(tenant); return FakeGraph(tenant)
        ctx = ToolContext(tenant_id="*", actor="t", client_factory=factory)
        import unittest.mock as mock
        with mock.patch("execution.core.config.get_config", return_value=cfg):
            r = m365_list_users.run(ctx, top=50)
        self.assertEqual(r["scope"], "all_clients")
        self.assertEqual(sorted(seen), ["acme", "globex"])
        self.assertEqual(r["count"], 2)
        self.assertEqual({u["tenant"] for u in r["users"]}, {"acme", "globex"})

    def test_search_uses_graph_search(self):
        from execution.core.context import ToolContext
        from execution.skills import m365_list_users

        class FakeGraph:
            def get(self, path, params=None):
                self.params = params
                return {"value": [{"id": "1", "displayName": "John Smith"}], "@odata.count": 1}
        fake = FakeGraph()
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)
        r = m365_list_users.run(ctx, search="smith")
        self.assertIn("$search", fake.params)
        self.assertIn("displayName:smith", fake.params["$search"])
        self.assertEqual(fake.params["$count"], "true")
        self.assertEqual(r["searched_for"], "smith")

    def test_name_contains_filters_substring_across_pages(self):
        # D-93: substring match across display name + UPN, case-insensitive, paging the whole
        # directory, complete in ONE tool call (vs the model looping m365_list_users to the cap).
        from execution.core.context import ToolContext
        from execution.skills import m365_list_users
        page1 = {"value": [
            {"id": "1", "displayName": "zzz_Old User", "userPrincipalName": "olduser@x.com"},
            {"id": "2", "displayName": "Alice", "userPrincipalName": "alice@x.com"}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?$skiptoken=ABC"}
        page2 = {"value": [
            {"id": "3", "displayName": "Bob", "userPrincipalName": "ZZZ_bob@x.com"},
            {"id": "4", "displayName": "Carol", "userPrincipalName": "carol@x.com"}]}

        class FakeGraph:
            def __init__(self): self.calls = []
            def get(self, path, params=None):
                self.calls.append(dict(params or {}))
                return page2 if (params or {}).get("$skiptoken") else page1
        fake = FakeGraph()
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)
        r = m365_list_users.run(ctx, name_contains="ZZZ_")     # mixed case in the query too
        self.assertEqual(r["match"], "contains")
        self.assertEqual(r["scanned"], 4)                      # paged through both pages
        self.assertEqual(len(fake.calls), 2)                   # followed nextLink once
        self.assertEqual({u["userPrincipalName"] for u in r["users"]},
                         {"olduser@x.com", "ZZZ_bob@x.com"})   # name hit + UPN hit
        self.assertNotIn("id", r["users"][0])                  # slimmed for context (D-94)
        self.assertEqual(r["count"], 2)

    def test_large_match_set_fits_model_context_budget(self):
        # D-94: 140 slimmed matches must serialize UNDER the agent's 20KB tool-result cap, so the
        # whole list reaches the model in ONE result (no truncation → no re-call loop).
        import json
        from execution.core.context import ToolContext
        from execution.skills import m365_list_users
        from execution.agent import tool_payload, MAX_RESULT_CHARS
        big = {"value": [{"id": f"id-{i:040d}",
                          "displayName": f"zzz_User Number {i}",
                          "userPrincipalName": f"zzz_user{i}@rhoresidential.com",
                          "mail": f"zzz_user{i}@rhoresidential.com",
                          "accountEnabled": False, "jobTitle": None, "department": None}
                         for i in range(140)]}
        fake = type("F", (), {"get": lambda self, p, params=None: big})()
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)
        r = m365_list_users.run(ctx, name_contains="zzz_")
        self.assertEqual(r["count"], 140)
        env = {"ok": True, "source": "m365", "tenant_id": "acme", "data": r}
        self.assertLessEqual(len(json.dumps(env, default=str)), MAX_RESULT_CHARS)  # no truncation
        self.assertNotIn("_truncated", tool_payload(env))

    def test_big_tenant_notes_total(self):
        from execution.core.context import ToolContext
        from execution.skills import m365_list_users

        class FakeGraph:
            def get(self, path, params=None):
                return {"value": [{"id": str(i)} for i in range(200)], "@odata.count": 240}
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: FakeGraph())
        r = m365_list_users.run(ctx, top=200)
        self.assertEqual(r["count"], 200)
        self.assertEqual(r["total_in_tenant"], 240)
        self.assertIn("narrow with", r["note"])

    def test_scope_allowlist(self):
        from execution.clients.scopes import is_allowed_read
        self.assertTrue(is_allowed_read("m365", "/users")[0])
        self.assertFalse(is_allowed_read("m365", "/applications")[0])


class ExchangeService(unittest.TestCase):
    """D-41 — 'exo' is a SECOND per-client connection sharing the device-code machinery."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = StubCfg({}, self.tmp.name)
        m365_auth._flows.clear()
        self._orig = m365_auth._form_post

    def tearDown(self):
        m365_auth._form_post = self._orig
        m365_auth._flows.clear()
        self.tmp.cleanup()

    def test_services_are_isolated_stores(self):
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "r-graph"})
        m365_auth.save_tokens(self.cfg, "acme", {"refresh_token": "r-exo"}, service="exo")
        self.assertEqual(m365_auth.load_tokens(self.cfg, "acme")["refresh_token"], "r-graph")
        self.assertEqual(m365_auth.load_tokens(self.cfg, "acme", "exo")["refresh_token"], "r-exo")
        self.assertTrue((Path(self.tmp.name) / "clients" / "acme" / "exo.json").is_file())
        # disconnecting Exchange leaves Graph intact
        self.assertTrue(m365_auth.clear_tokens(self.cfg, "acme", service="exo"))
        self.assertFalse(m365_auth.is_connected(self.cfg, "acme", service="exo"))
        self.assertTrue(m365_auth.is_connected(self.cfg, "acme"))

    def test_exo_signin_uses_exchange_app_and_audience(self):
        seen = {}
        def t(url, fields, timeout=30):
            seen.update(fields)
            return 200, {"device_code": "D", "user_code": "U", "interval": 5, "expires_in": 900}
        m365_auth._form_post = t
        flow = m365_auth.start_device_auth(self.cfg, "acme", service="exo")
        self.assertEqual(flow["service"], "exo")
        self.assertEqual(seen["client_id"], m365_auth.EXO_PS_CLIENT_ID)
        self.assertIn("outlook.office365.com/.default", seen["scope"])

    def test_exo_flow_persists_under_exo_and_captures_upn(self):
        m365_auth._form_post = lambda url, fields, timeout=30: (200, {
            "device_code": "D", "user_code": "U", "interval": 5, "expires_in": 900})
        flow = m365_auth.start_device_auth(self.cfg, "acme", service="exo")
        access = _jwt(int(time.time()) + 3600, tid="acme-tid")  # no upn claim in this fake jwt
        m365_auth._form_post = lambda url, fields, timeout=30: (200, {
            "access_token": access, "refresh_token": "R-EXO"})
        status, tenant = m365_auth.poll_device_auth(flow["flow_id"], self.cfg)
        self.assertEqual((status, tenant), ("connected", "acme"))
        self.assertTrue(m365_auth.is_connected(self.cfg, "acme", service="exo"))
        self.assertFalse(m365_auth.is_connected(self.cfg, "acme"))   # Graph untouched
        self.assertEqual(m365_auth.list_connected(self.cfg, "exo"), ["acme"])
        self.assertEqual(m365_auth.list_connected(self.cfg), [])

    def test_renew_all_covers_both_services(self):
        m365_auth.save_tokens(self.cfg, "acme", {
            "access_token": _jwt(int(time.time()) + 3600), "refresh_token": "a"})
        m365_auth.save_tokens(self.cfg, "acme", {
            "access_token": _jwt(int(time.time()) + 3600), "refresh_token": "b"}, service="exo")
        m365_auth._form_post = lambda url, fields, timeout=30: (200, {
            "access_token": _jwt(int(time.time()) + 3600), "refresh_token": "rotated"})
        r = m365_auth.renew_all(self.cfg)
        self.assertIn("acme", r["ok"])
        self.assertIn("acme (Exchange Online)", r["ok"])
        self.assertEqual(r["failed"], [])


class EXOClientTests(unittest.TestCase):
    """D-41 — InvokeCommand client: hard cmdlet allowlist, forced Shared, no destructive verbs."""

    def _client(self, sink, reply=None):
        from execution.clients.exo import EXOClient
        return EXOClient(lambda: "TOK", "tid-1", "admin@x.com",
                         transport=lambda m, u, headers=None, json_body=None, **_:
                         sink.append((m, u, headers, json_body)) or (200, reply or {"value": []}))

    def test_unknown_and_destructive_cmdlets_refused_before_http(self):
        calls = []
        c = self._client(calls)
        self.assertIn("not in the EXO allowlist", c.invoke("Remove-Mailbox", {"Identity": "x"})["error"])
        self.assertIn("not in the EXO allowlist", c.invoke("Remove-MailboxDatabase", {})["error"])
        self.assertIn("not in the EXO allowlist", c.invoke("Invoke-Expression", {})["error"])
        self.assertEqual(calls, [])                       # nothing ever left the box

    def test_set_mailbox_params_are_allowlisted(self):
        # D-55: Set-Mailbox is allowed but parameter-bounded — litigation hold, audit bypass
        # etc. are refused before HTTP even though the cmdlet itself is allow-listed.
        calls = []
        c = self._client(calls)
        r = c.invoke("Set-Mailbox", {"Identity": "a@demodomain.com",
                                     "LitigationHoldEnabled": True})
        self.assertIn("not in the allowlist", r["error"])
        self.assertEqual(calls, [])
        c.invoke("Set-Mailbox", {"Identity": "a@demodomain.com",
                                 "HiddenFromAddressListsEnabled": True, "Confirm": False})
        self.assertEqual(len(calls), 1)                   # allow-listed params go through

    def test_archive_toggles_forced_archive_param(self):
        # D-55: Enable-/Disable-Mailbox are pinned to ARCHIVE operations — this connector
        # can touch the online archive but can never mailbox-disable/-enable an account.
        calls = []
        c = self._client(calls)
        c.invoke("Disable-Mailbox", {"Identity": "a@demodomain.com", "Archive": False})
        c.invoke("Enable-Mailbox", {"Identity": "a@demodomain.com"})
        for _m, _u, _h, body in calls:
            self.assertIs(body["CmdletInput"]["Parameters"]["Archive"], True)
        # AutoExpandingArchive is the OTHER archive switch (a different Exchange parameter
        # set) — when it's the operation, Archive must NOT be forced alongside it.
        calls.clear()
        c.invoke("Enable-Mailbox", {"Identity": "a@demodomain.com",
                                    "AutoExpandingArchive": True})
        params = calls[0][3]["CmdletInput"]["Parameters"]
        self.assertIs(params["AutoExpandingArchive"], True)
        self.assertNotIn("Archive", params)
        # and both cmdlets are now parameter-allowlisted
        self.assertIn("not in the allowlist",
                      c.invoke("Enable-Mailbox", {"Identity": "a@demodomain.com",
                                                  "Database": "db1"})["error"])

    def test_hashtable_wire_shape(self):
        from execution.clients.exo import hashtable
        h = hashtable({"Add": "smtp:alias@demodomain.com"})
        self.assertEqual(h["@odata.type"], "#Exchange.GenericHashTable")
        self.assertEqual(h["Add"], "smtp:alias@demodomain.com")

    def test_new_mailbox_forces_shared(self):
        calls = []
        c = self._client(calls, reply={"value": [{"Name": "ai-test"}]})
        c.invoke("New-Mailbox", {"Name": "ai-test", "Shared": False})  # even an explicit False
        body = calls[0][3]["CmdletInput"]
        self.assertEqual(body["CmdletName"], "New-Mailbox")
        self.assertIs(body["Parameters"]["Shared"], True)

    def test_invoke_sends_bearer_anchor_and_tenant_route(self):
        calls = []
        c = self._client(calls, reply={"value": [{"DomainName": "x.com"}]})
        r = c.invoke("Get-AcceptedDomain")
        self.assertEqual(r, [{"DomainName": "x.com"}])
        m, u, headers, body = calls[0]
        self.assertIn("/tid-1/InvokeCommand", u)
        self.assertEqual(headers["Authorization"], "Bearer TOK")
        self.assertEqual(headers["X-AnchorMailbox"], "UPN:admin@x.com")

    def test_build_fails_closed(self):
        from execution.clients.exo import build_exo
        cfg = StubCfg({}, tempfile.mkdtemp())
        with self.assertRaises(MissingCredential):
            build_exo(cfg, "*")
        with self.assertRaises(MissingCredential):
            build_exo(cfg, "acme")                        # Exchange not signed in

    def test_destructive_cmdlets_split_from_invoke(self):
        # D-54: invoke() refuses Remove-Mailbox even though it exists; only invoke_destructive
        # reaches it, and that path refuses anything not in the destructive set.
        calls = []
        c = self._client(calls, reply={"value": [{"ok": True}]})
        self.assertIn("not in the EXO allowlist", c.invoke("Remove-Mailbox", {"Identity": "x"})["error"])
        self.assertEqual(calls, [])
        r = c.invoke_destructive("Remove-Mailbox", {"Identity": "shared@demodomain.com"})
        self.assertEqual(r, [{"ok": True}])
        self.assertEqual(calls[0][3]["CmdletInput"]["CmdletName"], "Remove-Mailbox")
        self.assertIn("not in the EXO destructive allowlist",
                      c.invoke_destructive("Remove-Mailbox-Database", {})["error"])
        self.assertIn("not in the EXO destructive allowlist",
                      c.invoke_destructive("Get-Mailbox", {})["error"])   # reads don't ride this path


class EXOSkills(unittest.TestCase):
    def _ctx(self, fake):
        from execution.core.context import ToolContext
        return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)

    def test_create_shared_mailbox_full_flow(self):
        from execution.skills import exo_create_shared_mailbox

        class FakeEXO:
            def __init__(self): self.calls = []
            def invoke(self, cmdlet, params=None):
                self.calls.append((cmdlet, params)); return [{"ok": True}]
        fake = FakeEXO()
        r = exo_create_shared_mailbox.run(
            self._ctx(fake), email="shared@demodomain.com",
            full_access_to="user@demodomain.com", send_as_to="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        names = [c[0] for c in fake.calls]
        self.assertEqual(names, ["New-Mailbox", "Add-MailboxPermission", "Add-RecipientPermission"])
        self.assertEqual(fake.calls[0][1]["PrimarySmtpAddress"], "shared@demodomain.com")
        self.assertEqual(fake.calls[1][1]["AccessRights"], ["FullAccess"])
        self.assertTrue(fake.calls[1][1]["AutoMapping"])
        self.assertEqual(fake.calls[2][1]["AccessRights"], ["SendAs"])

    def test_create_stops_on_create_failure(self):
        from execution.skills import exo_create_shared_mailbox

        class FakeEXO:
            def __init__(self): self.calls = []
            def invoke(self, cmdlet, params=None):
                self.calls.append(cmdlet); return {"error": "EXO HTTP 403 (role needed)"}
        fake = FakeEXO()
        r = exo_create_shared_mailbox.run(self._ctx(fake), email="shared@demodomain.com",
                                          full_access_to="user@demodomain.com")
        self.assertFalse(r["ok"])
        self.assertEqual(r["step"], "create")
        self.assertEqual(fake.calls, ["New-Mailbox"])     # no grants after a failed create

    def test_create_rejects_bad_email(self):
        from execution.skills import exo_create_shared_mailbox
        r = exo_create_shared_mailbox.run(self._ctx(None), email="not-an-email")
        self.assertFalse(r["ok"])

    def test_delete_mailbox_full_flow(self):
        # D-54: preflight type check → Remove-Mailbox via the destructive path → verify gone.
        from execution.skills import exo_delete_mailbox

        class FakeEXO:
            def __init__(self): self.calls = []; self.deleted = False
            def invoke(self, cmdlet, params=None):
                self.calls.append((cmdlet, params))
                if self.deleted:
                    return {"error": 'EXO HTTP 404 NotFound: mailbox couldn\'t be found'}
                return [{"RecipientTypeDetails": "SharedMailbox"}]
            def invoke_destructive(self, cmdlet, params=None):
                self.calls.append((cmdlet, params)); self.deleted = True; return [{"ok": True}]
        fake = FakeEXO()
        r = exo_delete_mailbox.run(self._ctx(fake), identity="shared@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual([c[0] for c in fake.calls],
                         ["Get-Mailbox", "Remove-Mailbox", "Get-Mailbox"])
        self.assertEqual(r["mailbox_type"], "SharedMailbox")
        self.assertIn("soft_delete", r["mode"])

    def test_delete_mailbox_never_claims_unverified_success(self):
        from execution.skills import exo_delete_mailbox

        class FakeEXO:                                    # delete "succeeds" but mailbox persists
            def invoke(self, cmdlet, params=None):
                return [{"RecipientTypeDetails": "SharedMailbox"}]
            def invoke_destructive(self, cmdlet, params=None):
                return [{"ok": True}]
        r = exo_delete_mailbox.run(self._ctx(FakeEXO()), identity="shared@demodomain.com")
        self.assertFalse(r["ok"])
        self.assertEqual(r["step"], "verify")

    def test_delete_mailbox_not_found_is_clean(self):
        from execution.skills import exo_delete_mailbox

        class FakeEXO:
            def invoke(self, cmdlet, params=None):
                return {"error": "EXO HTTP 404 NotFound: couldn't be found"}
            def invoke_destructive(self, cmdlet, params=None):
                raise AssertionError("must not delete a missing mailbox")
        r = exo_delete_mailbox.run(self._ctx(FakeEXO()), identity="ghost@demodomain.com")
        self.assertFalse(r["ok"])
        self.assertIn("nothing to delete", r["error"])

    def test_list_mailboxes_slims(self):
        from execution.skills import exo_list_mailboxes

        class FakeEXO:
            def invoke(self, cmdlet, params=None):
                assert cmdlet == "Get-Mailbox"
                return [{"DisplayName": "AI Test", "PrimarySmtpAddress": "shared@demodomain.com",
                         "RecipientTypeDetails": "SharedMailbox", "Junk": "drop-me"}]
        r = exo_list_mailboxes.run(self._ctx(FakeEXO()))
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["mailboxes"][0], {"display_name": "AI Test",
                                             "email": "shared@demodomain.com", "type": "SharedMailbox"})

    def test_list_mailboxes_not_found_is_clean_zero(self):
        from execution.skills import exo_list_mailboxes

        class FakeEXO:                                    # Get-Mailbox -Identity <missing> → 404
            def invoke(self, cmdlet, params=None):
                return {"error": 'EXO HTTP 404: {"error":{"code":"NotFound","message":'
                                 '"...object \'shared@demodomain.com\' couldn\'t be found..."}}'}
        r = exo_list_mailboxes.run(self._ctx(FakeEXO()), identity="shared@demodomain.com")
        self.assertEqual(r["count"], 0)
        self.assertEqual(r["mailboxes"], [])
        self.assertNotIn("error", r)                      # a missing lookup is not an error
        self.assertIn("no mailbox", r["note"])


class GatedWrites(unittest.TestCase):
    """D-40 — scoped_write bounds the reachable write surface (dispatch gates WHETHER it runs)."""

    def test_write_allowlist(self):
        from execution.clients.scopes import is_allowed_write
        self.assertTrue(is_allowed_write("m365", "/users")[0])
        self.assertTrue(is_allowed_write("m365", "/users/u-1/authentication/phoneMethods")[0])
        self.assertTrue(is_allowed_write("m365", "/users/u-1", "PATCH")[0])
        self.assertFalse(is_allowed_write("m365", "/users", "DELETE")[0])     # no delete, ever
        self.assertTrue(is_allowed_write("m365", "/groups/g-1/members/$ref")[0])       # D-56
        self.assertTrue(is_allowed_write("m365",
                        "/deviceManagement/importedWindowsAutopilotDeviceIdentities")[0])
        self.assertFalse(is_allowed_write("m365", "/organization")[0])        # not allow-listed
        self.assertFalse(is_allowed_write("m365", "/directoryRoles")[0])      # role writes: never
        self.assertFalse(is_allowed_write("kaseya", "/assetmgmt/agents")[0])  # vendor not opted in
        self.assertFalse(is_allowed_write("m365", "/users/../applications")[0])

    def test_scoped_write_calls_client_only_when_allowed(self):
        from execution.clients.scopes import scoped_write
        from execution.core.context import ToolContext

        class FakeGraph:
            def post(self, path, body=None):
                self.path, self.body = path, body
                return {"id": "new-user"}
        fake = FakeGraph()
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)
        r = scoped_write(ctx, "m365", "/users", body={"displayName": "Tommy Brown"})
        self.assertEqual(r["id"], "new-user")
        self.assertEqual(fake.path, "/users")
        blocked = scoped_write(ctx, "m365", "/applications", body={})
        self.assertIn("write blocked", blocked["error"])
        self.assertEqual(fake.path, "/users")           # client untouched by the blocked call

    def test_client_post_sends_bearer_and_body(self):
        from execution.clients.m365 import M365Client
        calls = []
        c = M365Client(lambda: "TOK",
                       transport=lambda m, u, headers=None, json_body=None, **_:
                       calls.append((m, u, headers, json_body)) or (201, {"id": "u-9"}))
        r = c.post("/users", {"displayName": "Tommy Brown"})
        self.assertEqual(r["id"], "u-9")
        m, u, headers, body = calls[0]
        self.assertEqual(m, "POST")
        self.assertEqual(headers["Authorization"], "Bearer TOK")
        self.assertEqual(body["displayName"], "Tommy Brown")
        self.assertEqual(c.post("../evil", {})["error"][:4], "path")


class ScriptedEXO:
    """Fake EXO client driven by a list of (expected_cmdlet, reply) in call order."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def invoke(self, cmdlet, params=None):
        self.calls.append((cmdlet, dict(params or {})))
        if not self.script:
            raise AssertionError(f"unexpected EXO call: {cmdlet}")
        want, reply = self.script.pop(0)
        if want != cmdlet:
            raise AssertionError(f"expected {want}, got {cmdlet}")
        return reply

    invoke_compliance = invoke               # same scripted queue for D-58 compliance calls


def _exo_ctx(fake):
    from execution.core.context import ToolContext
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


_MB = {"PrimarySmtpAddress": "user@demodomain.com", "DisplayName": "User",
       "RecipientTypeDetails": "UserMailbox", "HiddenFromAddressListsEnabled": False,
       "EmailAddresses": ["SMTP:user@demodomain.com"]}


class D55MailboxAdmin(unittest.TestCase):
    """D-55 — mailbox administration suite: every write verifies itself before claiming ok."""

    def test_create_shared_mailbox_pins_upn_and_names(self):
        # The AI-Test misfire: Exchange derived the User ID from the display name. Now the
        # UPN + Alias are pinned to the requested address and first/last names travel.
        # D-66: the sign-in param in New-Mailbox's Shared set is UserPrincipalName, NOT
        # MicrosoftOnlineServicesID (which conflicts with -Shared and breaks the call).
        from execution.skills import exo_create_shared_mailbox
        fake = ScriptedEXO([("New-Mailbox", [{"ok": True}])])
        r = exo_create_shared_mailbox.run(_exo_ctx(fake), email="ai-test@demodomain.com",
                                          display_name="AI Test",
                                          first_name="AI", last_name="Test")
        self.assertTrue(r["ok"], r)
        params = fake.calls[0][1]
        self.assertEqual(params["UserPrincipalName"], "ai-test@demodomain.com")
        self.assertNotIn("MicrosoftOnlineServicesID", params)   # would conflict with -Shared
        self.assertEqual(params["Alias"], "ai-test")
        self.assertEqual(params["FirstName"], "AI")
        self.assertEqual(params["LastName"], "Test")

    def test_gal_visibility_set_and_verified(self):
        from execution.skills import exo_set_gal_visibility
        hidden = {**_MB, "HiddenFromAddressListsEnabled": True}
        fake = ScriptedEXO([("Get-Mailbox", [_MB]), ("Set-Mailbox", {"ok": True}),
                            ("Get-Mailbox", [hidden])])
        r = exo_set_gal_visibility.run(_exo_ctx(fake), identity="user@demodomain.com",
                                       hidden=True)
        self.assertTrue(r["ok"], r)
        self.assertTrue(fake.calls[1][1]["HiddenFromAddressListsEnabled"])

    def test_bulk_gal_handles_mixed_states_in_one_call(self):
        # D-96: one call hides a whole list — skips already-hidden, hides+verifies the rest, flags
        # the ones that need cloud management, errors the missing — without N tool-call rounds.
        from execution.core.context import ToolContext
        from execution.skills import exo_bulk_set_gal_visibility as bulk

        class StatefulEXO:
            def __init__(self, mboxes): self.mboxes = mboxes; self.set_calls = []
            def invoke(self, cmdlet, params=None):
                params = params or {}; ident = str(params.get("Identity", "")).lower()
                if cmdlet == "Get-Mailbox":
                    mb = self.mboxes.get(ident)
                    return [mb] if mb else {"error": "couldn't be found"}
                if cmdlet == "Set-Mailbox":
                    self.set_calls.append(params); mb = self.mboxes.get(ident)
                    if mb and "HiddenFromAddressListsEnabled" in params:
                        mb["HiddenFromAddressListsEnabled"] = params["HiddenFromAddressListsEnabled"]
                    return {"ok": True}
                return {"error": "unexpected"}
        fake = StatefulEXO({
            "u1@x.com": {"PrimarySmtpAddress": "u1@x.com", "HiddenFromAddressListsEnabled": False,
                         "IsDirSynced": False, "IsExchangeCloudManaged": False},   # cloud-only → hide
            "u2@x.com": {"PrimarySmtpAddress": "u2@x.com", "HiddenFromAddressListsEnabled": True},  # already
            "u3@x.com": {"PrimarySmtpAddress": "u3@x.com", "HiddenFromAddressListsEnabled": False,
                         "IsDirSynced": True, "IsExchangeCloudManaged": False}})    # blocked
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)
        r = bulk.run(ctx, identities=["u1@x.com", "u2@x.com", "u3@x.com", "missing@x.com"],
                     hidden=True)
        self.assertTrue(r["ok"], r)
        status = {row["identity"]: row["status"] for row in r["results"]}
        self.assertEqual(status["u1@x.com"], "hidden")
        self.assertEqual(status["u2@x.com"], "unchanged")
        self.assertEqual(status["u3@x.com"], "needs_cloud_management")
        self.assertEqual(status["missing@x.com"], "error")
        self.assertEqual(r["summary"],
                         {"hidden": 1, "shown": 0, "unchanged": 1,
                          "needs_cloud_management": 1, "error": 1})
        self.assertEqual([p["Identity"] for p in fake.set_calls], ["u1@x.com"])  # only the one change

    def test_enable_cloud_management_set_and_verified(self):
        # D-91: synced mailbox starts on-prem-mastered; flip IsExchangeCloudManaged and verify.
        from execution.skills import exo_enable_cloud_management
        synced = {**_MB, "IsDirSynced": True, "IsExchangeCloudManaged": False}
        after = {**synced, "IsExchangeCloudManaged": True}
        fake = ScriptedEXO([("Get-Mailbox", [synced]),      # short-circuit preflight
                            ("Get-Mailbox", [synced]),      # set_and_verify preflight
                            ("Set-Mailbox", {"ok": True}),
                            ("Get-Mailbox", [after])])
        r = exo_enable_cloud_management.run(_exo_ctx(fake), identity="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertIs(fake.calls[2][1]["IsExchangeCloudManaged"], True)
        self.assertEqual(r["after"], {"IsExchangeCloudManaged": True})

    def test_enable_cloud_management_already_on_short_circuits(self):
        from execution.skills import exo_enable_cloud_management
        already = {**_MB, "IsDirSynced": True, "IsExchangeCloudManaged": True}
        fake = ScriptedEXO([("Get-Mailbox", [already])])    # no Set-Mailbox should run
        r = exo_enable_cloud_management.run(_exo_ctx(fake), identity="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertIn("already cloud-managed", r["note"])
        self.assertEqual(len(fake.calls), 1)                # nothing was written

    def test_mailbox_details_reports_cloud_management_status(self):
        # D-91: confirming "is cloud management set?" must be a READ — no write needed.
        from execution.skills import exo_mailbox_details
        mb = {**_MB, "IsDirSynced": True, "IsExchangeCloudManaged": False}
        fake = ScriptedEXO([("Get-Mailbox", [mb]),
                            ("Get-MailboxStatistics", [{"TotalItemSize": "1 MB", "ItemCount": 1}])])
        r = exo_mailbox_details.run(_exo_ctx(fake), identity="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["dir_synced"])
        self.assertFalse(r["cloud_managed"])

    def test_enable_cloud_management_param_is_allowlisted(self):
        # The one new Set-Mailbox parameter must be permitted by the client allowlist.
        from execution.clients.exo import PARAM_ALLOWLIST
        self.assertIn("IsExchangeCloudManaged", PARAM_ALLOWLIST["Set-Mailbox"])

    def test_gal_change_blocked_until_cloud_management_enabled(self):
        # D-91 follow-up: a directory-synced, non-cloud-managed mailbox can't have GAL changed in
        # EXO. Fail FAST in preflight with an actionable error, before any Set-Mailbox is sent.
        from execution.skills import exo_set_gal_visibility
        synced = {**_MB, "IsDirSynced": True, "IsExchangeCloudManaged": False}
        fake = ScriptedEXO([("Get-Mailbox", [synced])])     # ONLY the preflight read runs
        r = exo_set_gal_visibility.run(_exo_ctx(fake), identity="user@demodomain.com", hidden=True)
        self.assertFalse(r["ok"])
        self.assertTrue(r["needs_cloud_management"])
        self.assertIn("exo_enable_cloud_management", r["error"])
        self.assertEqual(len(fake.calls), 1)                # no Set-Mailbox attempted

    def test_gal_change_allowed_once_cloud_managed(self):
        from execution.skills import exo_set_gal_visibility
        base = {**_MB, "IsDirSynced": True, "IsExchangeCloudManaged": True}
        after = {**base, "HiddenFromAddressListsEnabled": True}
        fake = ScriptedEXO([("Get-Mailbox", [base]), ("Set-Mailbox", {"ok": True}),
                            ("Get-Mailbox", [after])])
        r = exo_set_gal_visibility.run(_exo_ctx(fake), identity="user@demodomain.com", hidden=True)
        self.assertTrue(r["ok"], r)

    def test_add_alias_blocked_until_cloud_management_enabled(self):
        from execution.skills import exo_add_alias
        synced = {**_MB, "IsDirSynced": True, "IsExchangeCloudManaged": False}
        fake = ScriptedEXO([("Get-Mailbox", [synced])])
        r = exo_add_alias.run(_exo_ctx(fake), identity="user@demodomain.com",
                              alias="sales@demodomain.com")
        self.assertFalse(r["ok"])
        self.assertTrue(r["needs_cloud_management"])
        self.assertEqual(len(fake.calls), 1)                # no Set-Mailbox attempted

    def test_write_that_does_not_stick_is_reported_as_failure(self):
        # D-43 lesson: Set-Mailbox returns no error but the re-read shows the old value.
        from execution.skills import exo_set_gal_visibility
        fake = ScriptedEXO([("Get-Mailbox", [_MB]), ("Set-Mailbox", {"ok": True}),
                            ("Get-Mailbox", [_MB])])          # unchanged after the "write"
        r = exo_set_gal_visibility.run(_exo_ctx(fake), identity="user@demodomain.com",
                                       hidden=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["step"], "verify")

    def test_quota_validates_and_matches_exchange_echo(self):
        from execution.skills import exo_set_mailbox_quota
        self.assertIn("not a size",
                      exo_set_mailbox_quota.run(_exo_ctx(None), identity="u@d.com",
                                                max_send="huge")["error"])
        self.assertIn("range",
                      exo_set_mailbox_quota.run(_exo_ctx(None), identity="u@d.com",
                                                max_send="500MB")["error"])
        # Exchange echoes "35 MB (36,700,160 bytes)" — the verify must still match "35MB"
        after = {**_MB, "MaxSendSize": "35 MB (36,700,160 bytes)"}
        fake = ScriptedEXO([("Get-Mailbox", [_MB]), ("Set-Mailbox", {"ok": True}),
                            ("Get-Mailbox", [after])])
        r = exo_set_mailbox_quota.run(_exo_ctx(fake), identity="user@demodomain.com",
                                      max_send="35MB")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.calls[1][1]["MaxSendSize"], "35MB")

    def test_forwarding_set_keep_copy_and_clear(self):
        from execution.skills import exo_set_forwarding
        fwd = {**_MB, "ForwardingSmtpAddress": "smtp:boss@demodomain.com",
               "DeliverToMailboxAndForward": True}
        fake = ScriptedEXO([("Get-Mailbox", [_MB]), ("Set-Mailbox", {"ok": True}),
                            ("Get-Mailbox", [fwd])])
        r = exo_set_forwarding.run(_exo_ctx(fake), identity="user@demodomain.com",
                                   forward_to="boss@demodomain.com", keep_copy=True)
        self.assertTrue(r["ok"], r)
        self.assertTrue(fake.calls[1][1]["DeliverToMailboxAndForward"])
        # empty forward_to → clears (None on the wire) and verifies it's off
        fake2 = ScriptedEXO([("Get-Mailbox", [fwd]), ("Set-Mailbox", {"ok": True}),
                             ("Get-Mailbox", [_MB])])
        r2 = exo_set_forwarding.run(_exo_ctx(fake2), identity="user@demodomain.com",
                                    forward_to="")
        self.assertTrue(r2["ok"], r2)
        self.assertIsNone(fake2.calls[1][1]["ForwardingSmtpAddress"])

    def test_convert_noop_when_already_target_type(self):
        from execution.skills import exo_convert_mailbox
        shared = {**_MB, "RecipientTypeDetails": "SharedMailbox"}
        fake = ScriptedEXO([("Get-Mailbox", [shared])])    # no Set-Mailbox follows
        r = exo_convert_mailbox.run(_exo_ctx(fake), identity="user@demodomain.com",
                                    to="shared")
        self.assertTrue(r["ok"])
        self.assertIn("already", r["note"])
        self.assertEqual(len(fake.calls), 1)

    def test_add_alias_verifies_and_skips_duplicates(self):
        from execution.skills import exo_add_alias
        withalias = {**_MB, "EmailAddresses": ["SMTP:user@demodomain.com",
                                               "smtp:alias@demodomain.com"]}
        fake = ScriptedEXO([("Get-Mailbox", [_MB]), ("Set-Mailbox", {"ok": True}),
                            ("Get-Mailbox", [withalias])])
        r = exo_add_alias.run(_exo_ctx(fake), identity="user@demodomain.com",
                              alias="alias@demodomain.com")
        self.assertTrue(r["ok"], r)
        ht = fake.calls[1][1]["EmailAddresses"]
        self.assertEqual(ht["@odata.type"], "#Exchange.GenericHashTable")
        self.assertEqual(ht["Add"], "smtp:alias@demodomain.com")
        # already present → clean no-op, no write
        fake2 = ScriptedEXO([("Get-Mailbox", [withalias])])
        r2 = exo_add_alias.run(_exo_ctx(fake2), identity="user@demodomain.com",
                               alias="alias@demodomain.com")
        self.assertTrue(r2["ok"])
        self.assertIn("already", r2["note"])

    def test_retention_policy_must_exist(self):
        from execution.skills import exo_set_retention_policy
        fake = ScriptedEXO([("Get-RetentionPolicy", [{"Name": "Default MRM Policy"}])])
        r = exo_set_retention_policy.run(_exo_ctx(fake), identity="user@demodomain.com",
                                         policy="No Such Policy")
        self.assertFalse(r["ok"])
        self.assertIn("Default MRM Policy", r["error"])

    def test_add_group_member_resolves_unified_groups(self):
        from execution.skills import exo_add_group_member
        member_row = {"PrimarySmtpAddress": "user@demodomain.com"}
        fake = ScriptedEXO([
            ("Get-DistributionGroup", {"error": "HTTP 404 ManagementObjectNotFound"}),
            ("Get-UnifiedGroup", [{"PrimarySmtpAddress": "team@demodomain.com"}]),
            ("Get-UnifiedGroupLinks", []),                 # not a member yet
            ("Add-UnifiedGroupLinks", {"ok": True}),
            ("Get-UnifiedGroupLinks", [member_row]),       # verified
        ])
        r = exo_add_group_member.run(_exo_ctx(fake), group="team@demodomain.com",
                                     member="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["kind"], "microsoft365")
        add = fake.calls[3][1]
        self.assertEqual(add["LinkType"], "Members")
        self.assertEqual(add["Links"], ["user@demodomain.com"])

    def test_mailbox_details_includes_archive_usage_only_when_enabled(self):
        from execution.skills import exo_mailbox_details
        archived = {**_MB, "ArchiveGuid": "11111111-1111-1111-1111-111111111111",
                    "ArchiveState": "Local"}
        fake = ScriptedEXO([
            ("Get-Mailbox", [archived]),
            ("Get-MailboxStatistics", [{"TotalItemSize": "1.2 GB", "ItemCount": 1000}]),
            ("Get-MailboxStatistics", [{"TotalItemSize": "3.4 GB", "ItemCount": 5000}]),
        ])
        r = exo_mailbox_details.run(_exo_ctx(fake), identity="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["archive"], "enabled")
        self.assertEqual(r["usage"]["size"], "1.2 GB")
        self.assertEqual(r["archive_usage"]["size"], "3.4 GB")
        self.assertIs(fake.calls[2][1].get("Archive"), True)


class D55RetentionManagement(unittest.TestCase):
    """D-55 follow-up — retention tags + policies: create, bundle, edit links."""

    _TAGS = [{"Name": "Delete after 90 days", "Type": "All",
              "RetentionAction": "DeleteAndAllowRecovery", "AgeLimitForRetention": 90,
              "RetentionEnabled": True}]

    def test_create_tag_validates_and_verifies(self):
        from execution.skills import exo_create_retention_tag as t
        made = self._TAGS + [{"Name": "Archive after 2y", "Type": "All",
                              "RetentionAction": "MoveToArchive"}]
        fake = ScriptedEXO([("Get-RetentionPolicyTag", self._TAGS),
                            ("New-RetentionPolicyTag", {"ok": True}),
                            ("Get-RetentionPolicyTag", made)])
        r = t.run(_exo_ctx(fake), name="Archive after 2y", applies_to="All",
                  action="move_to_archive", age_days=730)
        self.assertTrue(r["ok"], r)
        params = fake.calls[1][1]
        self.assertEqual(params["RetentionAction"], "MoveToArchive")
        self.assertEqual(params["AgeLimitForRetention"], 730)
        # archive tags only for All/Personal/RecoverableItems — Exchange's rule, before any call
        r2 = t.run(_exo_ctx(ScriptedEXO([])), name="x", applies_to="Inbox",
                   action="move_to_archive", age_days=30)
        self.assertIn("RecoverableItems", r2["error"])
        # D-66: RecoverableItems IS a valid move_to_archive scope (was wrongly rejected before)
        made_ri = [{"Name": "RI archive", "Type": "RecoverableItems"}]
        fake_ri = ScriptedEXO([("Get-RetentionPolicyTag", []),
                               ("New-RetentionPolicyTag", {"ok": True}),
                               ("Get-RetentionPolicyTag", made_ri)])
        r3 = t.run(_exo_ctx(fake_ri), name="RI archive", applies_to="RecoverableItems",
                   action="move_to_archive", age_days=365)
        self.assertTrue(r3["ok"], r3)
        # duplicate name refused
        fake3 = ScriptedEXO([("Get-RetentionPolicyTag", self._TAGS)])
        r3 = t.run(_exo_ctx(fake3), name="delete after 90 days", applies_to="All",
                   action="delete_allow_recovery", age_days=90)
        self.assertIn("already exists", r3["error"])

    def test_create_tag_permanent_delete_carries_warning(self):
        from execution.skills import exo_create_retention_tag as t
        made = [{"Name": "Purge 7y", "Type": "All"}]
        fake = ScriptedEXO([("Get-RetentionPolicyTag", []),
                            ("New-RetentionPolicyTag", {"ok": True}),
                            ("Get-RetentionPolicyTag", made)])
        r = t.run(_exo_ctx(fake), name="Purge 7y", applies_to="All",
                  action="permanent_delete", age_days=2555)
        self.assertTrue(r["ok"], r)
        self.assertIn("NOT recoverable", r["warning"])

    def test_create_policy_requires_existing_tags(self):
        from execution.skills import exo_create_retention_policy as p
        fake = ScriptedEXO([("Get-RetentionPolicy", []),
                            ("Get-RetentionPolicyTag", self._TAGS)])
        r = p.run(_exo_ctx(fake), name="Standard", tags=["No Such Tag"])
        self.assertFalse(r["ok"])
        self.assertIn("Delete after 90 days", r["error"])   # valid choices listed

    def test_create_policy_full_flow(self):
        from execution.skills import exo_create_retention_policy as p
        fake = ScriptedEXO([
            ("Get-RetentionPolicy", []),
            ("Get-RetentionPolicyTag", self._TAGS),
            ("New-RetentionPolicy", {"ok": True}),
            ("Get-RetentionPolicy", [{"Name": "Standard",
                                      "RetentionPolicyTagLinks": ["Delete after 90 days"]}]),
        ])
        r = p.run(_exo_ctx(fake), name="Standard", tags=["delete after 90 days"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.calls[2][1]["RetentionPolicyTagLinks"],
                         ["Delete after 90 days"])           # canonical casing sent
        self.assertEqual(r["tags"], ["Delete after 90 days"])

    def test_update_policy_tags_add_remove_verified(self):
        from execution.skills import exo_update_retention_policy_tags as u
        fake = ScriptedEXO([
            ("Get-RetentionPolicy", [{"Name": "Standard",
                                      "RetentionPolicyTagLinks": ["Old Tag"]}]),
            ("Get-RetentionPolicyTag", self._TAGS),
            ("Set-RetentionPolicy", {"ok": True}),
            ("Get-RetentionPolicy", [{"Name": "Standard",
                                      "RetentionPolicyTagLinks": ["Delete after 90 days"]}]),
        ])
        r = u.run(_exo_ctx(fake), policy="Standard",
                  add_tags=["Delete after 90 days"], remove_tags=["Old Tag"])
        self.assertTrue(r["ok"], r)
        ht = fake.calls[2][1]["RetentionPolicyTagLinks"]
        self.assertEqual(ht["@odata.type"], "#Exchange.GenericHashTable")
        self.assertEqual(ht["Add"], ["Delete after 90 days"])
        self.assertEqual(ht["Remove"], ["Old Tag"])
        self.assertEqual(r["tags_now"], ["Delete after 90 days"])
        # a removal that didn't stick is a verify failure
        fake2 = ScriptedEXO([
            ("Get-RetentionPolicy", [{"Name": "Standard",
                                      "RetentionPolicyTagLinks": ["Old Tag"]}]),
            ("Set-RetentionPolicy", {"ok": True}),
            ("Get-RetentionPolicy", [{"Name": "Standard",
                                      "RetentionPolicyTagLinks": ["Old Tag"]}]),
        ])
        r2 = u.run(_exo_ctx(fake2), policy="Standard", remove_tags=["Old Tag"])
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["step"], "verify")


class D55AutoExpandingArchive(unittest.TestCase):
    """D-55 follow-up — enable-only (Microsoft: cannot be turned off once on)."""

    def test_requires_regular_archive_first(self):
        from execution.skills import exo_enable_autoexpanding_archive as ax
        fake = ScriptedEXO([("Get-Mailbox", [_MB])])      # no ArchiveGuid → no archive
        r = ax.run(_exo_ctx(fake), identity="user@demodomain.com")
        self.assertFalse(r["ok"])
        self.assertIn("exo_set_archive", r["error"])

    def test_enables_and_verifies(self):
        from execution.skills import exo_enable_autoexpanding_archive as ax
        archived = {**_MB, "ArchiveGuid": "11111111-1111-1111-1111-111111111111",
                    "ArchiveState": "Local"}
        after = {**archived, "AutoExpandingArchiveEnabled": True}
        fake = ScriptedEXO([("Get-Mailbox", [archived]),
                            ("Enable-Mailbox", {"ok": True}),
                            ("Get-Mailbox", [after])])
        r = ax.run(_exo_ctx(fake), identity="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertIs(fake.calls[1][1]["AutoExpandingArchive"], True)
        self.assertNotIn("Archive", fake.calls[1][1])     # the skill sends the right switch
        self.assertIn("permanent", r["note"])
        # already on → clean no-op
        fake2 = ScriptedEXO([("Get-Mailbox", [after])])
        r2 = ax.run(_exo_ctx(fake2), identity="user@demodomain.com")
        self.assertTrue(r2["ok"])
        self.assertIn("already", r2["note"])

    def test_unverified_enable_is_failure(self):
        from execution.skills import exo_enable_autoexpanding_archive as ax
        archived = {**_MB, "ArchiveGuid": "11111111-1111-1111-1111-111111111111",
                    "ArchiveState": "Local"}
        fake = ScriptedEXO([("Get-Mailbox", [archived]),
                            ("Enable-Mailbox", {"ok": True}),
                            ("Get-Mailbox", [archived])])  # flag still off
        r = ax.run(_exo_ctx(fake), identity="user@demodomain.com")
        self.assertFalse(r["ok"])
        self.assertEqual(r["step"], "verify")


class D55GrantMailboxAccess(unittest.TestCase):
    """D-55 follow-up — full_access / send_as / send_on_behalf, each verified after the grant."""

    def test_full_access_granted_and_verified(self):
        from execution.skills import exo_grant_mailbox_access as g
        granted = [{"User": "user@demodomain.com", "AccessRights": ["FullAccess"]}]
        fake = ScriptedEXO([
            ("Get-Mailbox", [_MB]),
            ("Get-MailboxPermission", []),                # not granted yet
            ("Add-MailboxPermission", {"ok": True}),
            ("Get-MailboxPermission", granted),           # verified
        ])
        r = g.run(_exo_ctx(fake), mailbox="shared@demodomain.com",
                  user="user@demodomain.com", access="full_access")
        self.assertTrue(r["ok"], r)
        add = fake.calls[2][1]
        self.assertEqual(add["AccessRights"], ["FullAccess"])
        self.assertTrue(add["AutoMapping"])

    def test_send_as_already_granted_is_noop(self):
        from execution.skills import exo_grant_mailbox_access as g
        held = [{"Trustee": "user@demodomain.com", "AccessRights": ["SendAs"]}]
        fake = ScriptedEXO([("Get-Mailbox", [_MB]),
                            ("Get-RecipientPermission", held)])   # no Add follows
        r = g.run(_exo_ctx(fake), mailbox="shared@demodomain.com",
                  user="user@demodomain.com", access="send_as")
        self.assertTrue(r["ok"])
        self.assertIn("already", r["note"])
        self.assertEqual(len(fake.calls), 2)

    def test_send_on_behalf_uses_hashtable_and_verifies(self):
        from execution.skills import exo_grant_mailbox_access as g
        after = {**_MB, "GrantSendOnBehalfTo": ["User Person"]}
        fake = ScriptedEXO([
            ("Get-Mailbox", [_MB]),
            ("Set-Mailbox", {"ok": True}),
            ("Get-Mailbox", [after]),
        ])
        r = g.run(_exo_ctx(fake), mailbox="shared@demodomain.com",
                  user="user@demodomain.com", access="send_on_behalf")
        self.assertTrue(r["ok"], r)
        ht = fake.calls[1][1]["GrantSendOnBehalfTo"]
        self.assertEqual(ht["@odata.type"], "#Exchange.GenericHashTable")
        self.assertEqual(ht["Add"], "user@demodomain.com")

    def test_grant_that_does_not_stick_is_failure(self):
        from execution.skills import exo_grant_mailbox_access as g
        fake = ScriptedEXO([
            ("Get-Mailbox", [_MB]),
            ("Get-RecipientPermission", []),
            ("Add-RecipientPermission", {"ok": True}),
            ("Get-RecipientPermission", []),              # still missing after the "grant"
        ])
        r = g.run(_exo_ctx(fake), mailbox="shared@demodomain.com",
                  user="user@demodomain.com", access="send_as")
        self.assertFalse(r["ok"])
        self.assertEqual(r["step"], "verify")


class FakeGraph:
    """Fake M365Client: canned GET replies by path-prefix; records writes."""
    def __init__(self, gets):
        self.gets = gets          # {path_prefix: reply}
        self.writes = []

    def get(self, path, params=None):
        for prefix, reply in self.gets.items():
            if path.split("?")[0].startswith(prefix):
                return reply(path) if callable(reply) else reply
        return {"error": f"unexpected GET {path}"}

    def post(self, path, body=None):
        self.writes.append(("POST", path, body))
        return {"id": "u-1", **(body or {})}

    def patch(self, path, body=None):
        self.writes.append(("PATCH", path, body))
        return {"ok": True}


def _graph_ctx(fake):
    from execution.core.context import ToolContext
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


class D55GraphUsersAndLicenses(unittest.TestCase):
    _SKUS = {"value": [
        {"skuPartNumber": "O365_BUSINESS_PREMIUM", "skuId": "sku-guid-1",
         "prepaidUnits": {"enabled": 10}, "consumedUnits": 7, "capabilityStatus": "Enabled",
         "servicePlans": [
             {"servicePlanName": "EXCHANGE_S_STANDARD", "servicePlanId": "plan-exch",
              "provisioningStatus": "Success", "appliesTo": "User"},
             {"servicePlanName": "TEAMS1", "servicePlanId": "plan-teams",
              "provisioningStatus": "Success", "appliesTo": "User"},
             {"servicePlanName": "YAMMER_ENTERPRISE", "servicePlanId": "plan-yammer",
              "provisioningStatus": "Success", "appliesTo": "User"},
             {"servicePlanName": "MDE_SMB", "servicePlanId": "plan-company",
              "provisioningStatus": "Success", "appliesTo": "Company"},
         ]},
        {"skuPartNumber": "FULL_SKU", "skuId": "sku-guid-2",
         "prepaidUnits": {"enabled": 5}, "consumedUnits": 5, "capabilityStatus": "Enabled",
         "servicePlans": [
             {"servicePlanName": "EXCHANGE_S_STANDARD", "servicePlanId": "plan-exch",
              "provisioningStatus": "Success", "appliesTo": "User"},
         ]},
    ]}

    def test_create_user_upn_equals_email_and_password_generated(self):
        from execution.skills import m365_create_user
        fake = FakeGraph({"/users/": {"userPrincipalName": "ai-test@demodomain.com",
                                      "displayName": "AI Test"}})
        r = m365_create_user.run(_graph_ctx(fake), email="ai-test@demodomain.com",
                                 first_name="AI", last_name="Test",
                                 job_title="Tester", city="Tampa")
        self.assertTrue(r["ok"], r)
        method, path, body = fake.writes[0]
        self.assertEqual((method, path), ("POST", "/users"))
        # the misfire fix: User ID == the requested email, never display-name-derived
        self.assertEqual(body["userPrincipalName"], "ai-test@demodomain.com")
        self.assertEqual(body["mailNickname"], "ai-test")
        self.assertEqual(body["givenName"], "AI")
        self.assertEqual(body["surname"], "Test")
        self.assertEqual(body["jobTitle"], "Tester")
        self.assertEqual(body["city"], "Tampa")
        pw = body["passwordProfile"]["password"]
        self.assertGreaterEqual(len(pw), 16)
        self.assertTrue(body["passwordProfile"]["forceChangePasswordNextSignIn"])
        self.assertEqual(r["initial_password"], pw)
        self.assertEqual(r["user_id"], "ai-test@demodomain.com")

    def test_create_user_owner_password_used_but_never_echoed(self):
        # D-55 follow-up: an explicit password is set verbatim, must_change is honored,
        # and the password is NOT echoed back (results land in chat history).
        from execution.skills import m365_create_user
        fake = FakeGraph({"/users/": {"userPrincipalName": "x@demodomain.com"}})
        r = m365_create_user.run(_graph_ctx(fake), email="x@demodomain.com",
                                 first_name="A", last_name="B",
                                 password="Sup3r$ecretPW!", must_change=False)
        self.assertTrue(r["ok"], r)
        profile = fake.writes[0][2]["passwordProfile"]
        self.assertEqual(profile["password"], "Sup3r$ecretPW!")
        self.assertFalse(profile["forceChangePasswordNextSignIn"])
        self.assertNotIn("initial_password", r)
        self.assertNotIn("Sup3r$ecretPW!", str(r))
        # too-short owner password is refused before any write
        r2 = m365_create_user.run(_graph_ctx(FakeGraph({})), email="y@demodomain.com",
                                  first_name="A", last_name="B", password="short")
        self.assertFalse(r2["ok"])
        self.assertIn("8", r2["error"])

    def test_create_user_unverified_create_is_failure(self):
        from execution.skills import m365_create_user
        fake = FakeGraph({"/users/": {"error": "boom"}})   # read-back fails
        r = m365_create_user.run(_graph_ctx(fake), email="x@demodomain.com",
                                 first_name="A", last_name="B")
        self.assertFalse(r["ok"])
        self.assertEqual(r["step"], "verify")

    def test_list_licenses_math(self):
        from execution.skills import m365_list_licenses
        fake = FakeGraph({"/subscribedSkus": self._SKUS})
        r = m365_list_licenses.run(_graph_ctx(fake))
        by = {x["license"]: x for x in r["licenses"]}
        self.assertEqual(by["O365_BUSINESS_PREMIUM"]["available"], 3)
        self.assertEqual(by["FULL_SKU"]["available"], 0)

    def test_assign_license_sets_usage_location_and_verifies(self):
        from execution.skills import m365_assign_license
        user_states = iter([
            {"id": "u-1", "usageLocation": None, "assignedLicenses": []},   # preflight
            {"assignedLicenses": [{"skuId": "sku-guid-1"}]},                # verify
        ])
        fake = FakeGraph({"/subscribedSkus": self._SKUS,
                          "/users/": lambda p: next(user_states)})
        r = m365_assign_license.run(_graph_ctx(fake), user="user@demodomain.com",
                                    license="o365_business_premium")        # case-insensitive
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.writes[0], ("PATCH", "/users/user@demodomain.com",
                                          {"usageLocation": "US"}))
        self.assertEqual(fake.writes[1][1], "/users/user@demodomain.com/assignLicense")
        self.assertEqual(fake.writes[1][2]["addLicenses"][0]["skuId"], "sku-guid-1")

    def test_assign_license_refuses_when_no_seats(self):
        from execution.skills import m365_assign_license
        fake = FakeGraph({"/subscribedSkus": self._SKUS,
                          "/users/": {"id": "u-1", "usageLocation": "US",
                                      "assignedLicenses": []}})
        r = m365_assign_license.run(_graph_ctx(fake), user="user@demodomain.com",
                                    license="FULL_SKU")
        self.assertFalse(r["ok"])
        self.assertIn("no seats", r["error"])
        self.assertEqual(fake.writes, [])                  # refused before any write

    def test_list_license_apps(self):
        # D-55 follow-up: license=... shows the checkable apps inside that SKU.
        from execution.skills import m365_list_licenses
        fake = FakeGraph({"/subscribedSkus": self._SKUS})
        r = m365_list_licenses.run(_graph_ctx(fake), license="O365_BUSINESS_PREMIUM")
        self.assertEqual(r["app_count"], 4)
        by = {a["app"]: a for a in r["apps"]}
        self.assertEqual(by["TEAMS1"]["applies_to"], "User")
        self.assertEqual(by["MDE_SMB"]["applies_to"], "Company")
        bad = m365_list_licenses.run(_graph_ctx(fake), license="NOPE")
        self.assertIn("they own", bad["error"])

    def test_assign_with_unchecked_apps(self):
        from execution.skills import m365_assign_license
        states = iter([
            {"id": "u-1", "usageLocation": "US", "assignedLicenses": []},
            {"assignedLicenses": [{"skuId": "sku-guid-1",
                                   "disabledPlans": ["plan-yammer"]}]},
        ])
        fake = FakeGraph({"/subscribedSkus": self._SKUS,
                          "/users/": lambda p: next(states)})
        r = m365_assign_license.run(_graph_ctx(fake), user="user@demodomain.com",
                                    license="O365_BUSINESS_PREMIUM",
                                    disabled_apps=["yammer_enterprise"])   # case-insensitive
        self.assertTrue(r["ok"], r)
        body = fake.writes[0][2]
        self.assertEqual(body["addLicenses"][0]["disabledPlans"], ["plan-yammer"])
        self.assertEqual(r["apps_disabled"], ["YAMMER_ENTERPRISE"])
        self.assertEqual(r["apps_enabled"], ["EXCHANGE_S_STANDARD", "TEAMS1"])

    def test_assign_rejects_unknown_or_company_apps(self):
        from execution.skills import m365_assign_license
        fake = FakeGraph({"/subscribedSkus": self._SKUS})
        r = m365_assign_license.run(_graph_ctx(fake), user="user@demodomain.com",
                                    license="O365_BUSINESS_PREMIUM",
                                    disabled_apps=["MDE_SMB"])     # Company plan → not per-user
        self.assertFalse(r["ok"])
        self.assertIn("unknown app", r["error"])
        self.assertIn("TEAMS1", r["error"])                        # valid choices listed
        self.assertEqual(fake.writes, [])

    def test_update_apps_on_held_license_skips_seat_check(self):
        # The user already holds FULL_SKU (0 seats free) — editing its app checkboxes must
        # still work, because an update consumes no seat.
        from execution.skills import m365_assign_license
        states = iter([
            {"id": "u-1", "usageLocation": "US",
             "assignedLicenses": [{"skuId": "sku-guid-2", "disabledPlans": []}]},
            {"assignedLicenses": [{"skuId": "sku-guid-2",
                                   "disabledPlans": ["plan-exch"]}]},
        ])
        fake = FakeGraph({"/subscribedSkus": self._SKUS,
                          "/users/": lambda p: next(states)})
        r = m365_assign_license.run(_graph_ctx(fake), user="user@demodomain.com",
                                    license="FULL_SKU",
                                    disabled_apps=["EXCHANGE_S_STANDARD"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["license_updated"], "FULL_SKU")
        self.assertNotIn("seats_left_after", r)
        # …and a verify mismatch is reported as a failure, never silent success
        states2 = iter([
            {"id": "u-1", "usageLocation": "US",
             "assignedLicenses": [{"skuId": "sku-guid-2", "disabledPlans": []}]},
            {"assignedLicenses": [{"skuId": "sku-guid-2", "disabledPlans": []}]},  # unchanged
        ])
        fake2 = FakeGraph({"/subscribedSkus": self._SKUS,
                           "/users/": lambda p: next(states2)})
        r2 = m365_assign_license.run(_graph_ctx(fake2), user="user@demodomain.com",
                                     license="FULL_SKU",
                                     disabled_apps=["EXCHANGE_S_STANDARD"])
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["step"], "verify")


class D65RemoveCounterparts(unittest.TestCase):
    """D-65 — add/remove symmetry: removal cmdlets are param-allowlisted writes; verify-gone."""

    def test_removal_cmdlets_are_param_allowlisted_writes(self):
        from execution.clients.exo import ALLOWED_CMDLETS, PARAM_ALLOWLIST, DESTRUCTIVE_CMDLETS
        for c in ("Remove-DistributionGroupMember", "Remove-UnifiedGroupLinks",
                  "Remove-DistributionGroup", "Remove-MailContact", "Remove-RetentionPolicyTag",
                  "Remove-RetentionPolicy", "Remove-TransportRule", "Remove-InboundConnector"):
            self.assertEqual(ALLOWED_CMDLETS.get(c), "write", c)
            self.assertIn(c, PARAM_ALLOWLIST, c)
        self.assertEqual(DESTRUCTIVE_CMDLETS, frozenset({"Remove-Mailbox"}))  # still the only one

    def test_remove_alias_protects_primary_and_verifies(self):
        from execution.skills import exo_remove_alias as ra
        mb = {**_MB, "EmailAddresses": ["SMTP:user@demodomain.com",
                                        "smtp:alias@demodomain.com"]}
        # refuse removing the primary
        fake0 = ScriptedEXO([("Get-Mailbox", [mb])])
        self.assertIn("PRIMARY", ra.run(_exo_ctx(fake0), identity="user@demodomain.com",
                                        alias="user@demodomain.com")["error"])
        fake = ScriptedEXO([("Get-Mailbox", [mb]), ("Set-Mailbox", {"ok": True}),
                            ("Get-Mailbox", [_MB])])      # alias gone
        r = ra.run(_exo_ctx(fake), identity="user@demodomain.com",
                   alias="alias@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.calls[1][1]["EmailAddresses"]["Remove"], "smtp:alias@demodomain.com")

    def test_remove_group_member_unified(self):
        from execution.skills import exo_remove_group_member as rg
        member = {"PrimarySmtpAddress": "user@demodomain.com"}
        fake = ScriptedEXO([
            ("Get-DistributionGroup", {"error": "404 NotFound"}),
            ("Get-UnifiedGroup", [{"PrimarySmtpAddress": "team@demodomain.com"}]),
            ("Get-UnifiedGroupLinks", [member]),          # is a member
            ("Remove-UnifiedGroupLinks", {"ok": True}),
            ("Get-UnifiedGroupLinks", []),                # gone
        ])
        r = rg.run(_exo_ctx(fake), group="team@demodomain.com", member="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.calls[3][1]["Links"], ["user@demodomain.com"])

    def test_delete_object_noop_when_absent(self):
        from execution.skills import exo_delete_contact as dc
        fake = ScriptedEXO([("Get-MailContact", {"error": "404 NotFound"})])
        r = dc.run(_exo_ctx(fake), contact="ghost@vendor.com")
        self.assertTrue(r["ok"])
        self.assertIn("nothing to delete", r["note"])

    def test_delete_distribution_group_verifies_gone(self):
        from execution.skills import exo_delete_distribution_group as dg
        fake = ScriptedEXO([
            ("Get-DistributionGroup", [{"PrimarySmtpAddress": "team@demodomain.com"}]),
            ("Remove-DistributionGroup", {"ok": True}),
            ("Get-DistributionGroup", {"error": "404 NotFound"}),
        ])
        r = dg.run(_exo_ctx(fake), group="team@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["deleted"], "team@demodomain.com")

    def test_remove_proofpoint_both_parts(self):
        from execution.skills import exo_remove_proofpoint_bypass as pp
        fake = ScriptedEXO([
            ("Get-TransportRule", [{"Name": "x"}]),
            ("Remove-TransportRule", {"ok": True}),
            ("Get-TransportRule", {"error": "404"}),
            ("Get-InboundConnector", [{"Name": "y"}]),
            ("Remove-InboundConnector", {"ok": True}),
            ("Get-InboundConnector", {"error": "404"}),
        ])
        r = pp.run(_exo_ctx(fake))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["steps"]["spam_bypass_rule"], "removed")
        self.assertEqual(r["steps"]["inbound_connector"], "removed")

    def test_remove_forwarding_alert_uses_compliance(self):
        from execution.skills import exo_remove_forwarding_alert as fa
        fake = ScriptedEXO([
            ("Get-ProtectionAlert", [{"Name": "Forwarding/redirect rule was created"}]),
            ("Remove-ProtectionAlert", {"ok": True}),
            ("Get-ProtectionAlert", {"error": "404 NotFound"}),
        ])
        r = fa.run(_exo_ctx(fake), name="Forwarding/redirect rule was created")
        self.assertTrue(r["ok"], r)


class D67Hardening(unittest.TestCase):
    """D-67 — robustness hardening from the audit's non-blocking notes."""

    def test_is_group_member_uses_targeted_filter_no_999_cap(self):
        from execution.skills import _graph_common as g
        seen = {}

        class Fake(FakeGraph):
            def get(self, path, params=None):
                seen["path"] = path
                seen["params"] = params or {}
                return {"value": [{"id": "u-1"}]}        # filtered query returns the one match
        self.assertTrue(g.is_group_member(_graph_ctx(Fake({})), "g-1", "u-1"))
        # it asks a targeted filtered question, not a list-everything ($top 999) scan
        self.assertIn("id eq 'u-1'", seen["params"].get("$filter", ""))
        self.assertNotEqual(seen["params"].get("$top"), 999)

    def test_is_group_member_falls_back_when_filter_unsupported(self):
        from execution.skills import _graph_common as g
        calls = []

        class Fake(FakeGraph):
            def get(self, path, params=None):
                calls.append(params or {})
                if "$filter" in (params or {}):
                    return {"error": {"message": "filter not supported"}}
                return {"value": [{"id": "u-9"}]}        # paged scan finds it
        self.assertTrue(g.is_group_member(_graph_ctx(Fake({})), "g-1", "u-9"))
        self.assertEqual(len(calls), 2)                  # filtered try, then scan fallback

    def test_autopilot_lookup_handles_spaced_serial(self):
        from execution.skills import _graph_common as g
        calls = []

        class Fake(FakeGraph):
            def get(self, path, params=None):
                calls.append(params or {})
                # the contains() filter 400-equivalent → error; unfiltered scan succeeds
                if "$filter" in (params or {}):
                    return {"error": {"message": "Bad Request"}}
                return {"value": [{"id": "d-1", "serialNumber": "ABC 123"},
                                  {"id": "d-2", "serialNumber": "ZZZ 999"}]}
        dev, bad = g.find_autopilot_by_serial(_graph_ctx(Fake({})), "ABC 123")
        self.assertIsNone(bad)
        self.assertEqual(dev["id"], "d-1")               # exact client-side match

    def test_list_autopilot_spaced_serial_client_filters(self):
        from execution.skills import m365_list_autopilot_devices as ld

        class Fake(FakeGraph):
            def get(self, path, params=None):
                # spaced serial → no server $filter is sent; returns all, tool filters
                assert "$filter" not in (params or {}), "spaced serial must not hit the filter"
                return {"value": [{"serialNumber": "ABC 123", "id": "d-1"},
                                  {"serialNumber": "OTHER", "id": "d-2"}]}
        r = ld.run(_graph_ctx(Fake({})), serial="ABC 123")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["devices"][0]["serial"], "ABC 123")

    def test_sharepoint_list_flags_truncation(self):
        from execution.skills import m365_list_sharepoint_sites as ls
        fake = FakeGraph({"/sites": {"value": [{"displayName": "A", "webUrl": "https://x/sites/a"}],
                                     "@odata.nextLink": "https://graph/next"}})
        r = ls.run(_graph_ctx(fake))
        self.assertIn("more sites exist", r["note"])

    def test_quota_accepts_gb_units(self):
        from execution.skills import exo_set_mailbox_quota as q
        self.assertEqual(q._parse("max_send", "0.1GB")[0], "102MB")   # ~102 MB, within range
        self.assertIsNone(q._parse("max_send", "0.1GB")[1])
        self.assertIn("range", q._parse("max_send", "1GB")[1])        # 1024MB > 150MB ceiling
        self.assertEqual(q._parse("max_send", "35MB")[0], "35MB")     # unchanged


class D65GraphRemoves(unittest.TestCase):
    def test_delete_scope_allowlist(self):
        from execution.clients.scopes import is_allowed_delete
        self.assertTrue(is_allowed_delete("m365", "/groups/g-1")[0])
        self.assertTrue(is_allowed_delete("m365", "/groups/g-1/members/u-1/$ref")[0])
        self.assertTrue(is_allowed_delete("m365",
                        "/users/u@x.com/authentication/phoneMethods/m1")[0])
        self.assertFalse(is_allowed_delete("m365", "/applications/a-1")[0])  # not allow-listed
        self.assertFalse(is_allowed_delete("m365", "/groups/../applications")[0])
        self.assertFalse(is_allowed_delete("kaseya", "/x")[0])

    def test_graph_client_delete_routes(self):
        from execution.clients.m365 import M365Client
        calls = []
        c = M365Client(lambda: "TOK",
                       transport=lambda m, u, headers=None, **kw:
                       calls.append((m, u)) or (204, None))
        r = c.delete("/groups/g-1")
        self.assertEqual(calls[0], ("DELETE", "https://graph.microsoft.com/v1.0/groups/g-1"))
        self.assertTrue(r["ok"])

    def test_remove_security_group_member_refuses_dynamic(self):
        from execution.skills import m365_remove_security_group_member as rm
        fake = FakeGraph({"/groups": {"value": [
            {"id": "g-1", "displayName": "Dyn", "groupTypes": ["DynamicMembership"],
             "membershipRule": "(x)"}]}})
        r = rm.run(_graph_ctx(fake), group="Dyn", member="user@demodomain.com")
        self.assertFalse(r["ok"])
        self.assertIn("DYNAMIC", r["error"])

    def test_remove_security_group_member_deletes_ref(self):
        from execution.skills import m365_remove_security_group_member as rm
        member_lists = iter([{"value": [{"id": "u-1"}]}, {"value": []}])

        class Fake(FakeGraph):
            def get(self, path, params=None):
                if path.endswith("/members"):
                    return next(member_lists)
                if path == "/groups":
                    return {"value": [{"id": "g-1", "displayName": "HD", "groupTypes": []}]}
                if path.startswith("/users/"):
                    return {"id": "u-1", "userPrincipalName": "user@demodomain.com"}
                return {"error": f"unexpected {path}"}

            def delete(self, path, body=None):
                self.writes.append(("DELETE", path, None))
                return {"ok": True}
        fake = Fake({})
        r = rm.run(_graph_ctx(fake), group="HD", member="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.writes[0], ("DELETE", "/groups/g-1/members/u-1/$ref", None))

    def test_remove_security_group_member_polls_through_replication_lag(self):
        # D-104: the DELETE succeeds but the membership re-read is stale for a moment — the verify
        # must POLL, not false-fail on the first stale read.
        import os
        os.environ["MSPAI_VERIFY_DELAY"] = "0"
        self.addCleanup(lambda: os.environ.pop("MSPAI_VERIFY_DELAY", None))
        from execution.skills import m365_remove_security_group_member as rm
        # preflight: present · verify#1: STILL present (lag) · verify#2: gone
        member_lists = iter([{"value": [{"id": "u-1"}]},
                             {"value": [{"id": "u-1"}]},
                             {"value": []}])

        class Fake(FakeGraph):
            def get(self, path, params=None):
                if path.endswith("/members"):
                    return next(member_lists)
                if path == "/groups":
                    return {"value": [{"id": "g-1", "displayName": "HD", "groupTypes": [],
                                       "onPremisesSyncEnabled": False}]}
                if path.startswith("/users/"):
                    return {"id": "u-1", "userPrincipalName": "user@demodomain.com"}
                return {"error": f"unexpected {path}"}

            def delete(self, path, body=None):
                self.writes.append(("DELETE", path, None))
                return {"ok": True}
        fake = Fake({})
        r = rm.run(_graph_ctx(fake), group="HD", member="user@demodomain.com")
        self.assertTrue(r["ok"], r)                 # recovered via poll, not a false failure

    def test_remove_security_group_member_on_prem_synced_is_guarded(self):
        # D-103b: an AD-synced group can't be edited in the cloud — refuse up front, no DELETE.
        from execution.skills import m365_remove_security_group_member as rm
        fake = FakeGraph({"/groups": {"value": [
            {"id": "g-1", "displayName": "AD Group", "groupTypes": [],
             "onPremisesSyncEnabled": True}]}})
        r = rm.run(_graph_ctx(fake), group="AD Group", member="user@demodomain.com")
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("on_prem_synced"))
        self.assertEqual(fake.writes, [])           # no removal attempted

    def test_remove_license_inverse_of_assign(self):
        from execution.skills import m365_remove_license as rl
        skus = {"value": [{"skuPartNumber": "O365_BUSINESS_PREMIUM", "skuId": "sku-1",
                           "prepaidUnits": {"enabled": 10}, "consumedUnits": 5}]}
        states = iter([
            {"id": "u-1", "assignedLicenses": [{"skuId": "sku-1"}]},   # has it
            {"assignedLicenses": []},                                  # gone after
        ])
        fake = FakeGraph({"/subscribedSkus": skus, "/users/": lambda p: next(states)})
        r = rl.run(_graph_ctx(fake), user="user@demodomain.com",
                   license="O365_BUSINESS_PREMIUM")
        self.assertTrue(r["ok"], r)
        body = fake.writes[0][2]
        self.assertEqual(body["removeLicenses"], ["sku-1"])
        self.assertEqual(body["addLicenses"], [])

    def test_remove_phone_auth_deletes_method(self):
        from execution.skills import m365_remove_phone_auth as rp
        lists = iter([{"value": [{"id": "m1", "phoneType": "mobile",
                                  "phoneNumber": "+1 5551234567"}]},
                      {"value": []}])

        class Fake(FakeGraph):
            def get(self, path, params=None):
                return next(lists)

            def delete(self, path, body=None):
                self.writes.append(("DELETE", path, None))
                return {"ok": True}
        fake = Fake({})
        r = rp.run(_graph_ctx(fake), user="user@demodomain.com", phone_type="mobile")
        self.assertTrue(r["ok"], r)
        self.assertIn("phoneMethods/m1", fake.writes[0][1])


class D63RevokeAccess(unittest.TestCase):
    """D-63 — the revoke mirror of the grant tools; verify-gone, not-held = clean no-op."""

    def test_revoke_full_access_verified_gone(self):
        from execution.skills import exo_revoke_mailbox_access as rv
        held = [{"User": "tech@demodomain.com", "AccessRights": ["FullAccess"]}]
        fake = ScriptedEXO([
            ("Get-Mailbox", [_MB]),
            ("Get-MailboxPermission", held),
            ("Remove-MailboxPermission", {"ok": True}),
            ("Get-MailboxPermission", []),                # verified gone
        ])
        r = rv.run(_exo_ctx(fake), mailbox="info@demodomain.com",
                   user="tech@demodomain.com", access="full_access")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["access_revoked"], "full_access")
        self.assertEqual(fake.calls[2][1]["AccessRights"], ["FullAccess"])
        # still present after the call → loud verify failure
        fake2 = ScriptedEXO([
            ("Get-Mailbox", [_MB]),
            ("Get-MailboxPermission", held),
            ("Remove-MailboxPermission", {"ok": True}),
            ("Get-MailboxPermission", held),              # didn't stick
        ])
        r2 = rv.run(_exo_ctx(fake2), mailbox="info@demodomain.com",
                    user="tech@demodomain.com", access="full_access")
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["step"], "verify")

    def test_revoke_not_held_is_noop(self):
        from execution.skills import exo_revoke_mailbox_access as rv
        fake = ScriptedEXO([("Get-Mailbox", [_MB]),
                            ("Get-RecipientPermission", [])])   # no Remove follows
        r = rv.run(_exo_ctx(fake), mailbox="info@demodomain.com",
                   user="tech@demodomain.com", access="send_as")
        self.assertTrue(r["ok"])
        self.assertIn("nothing to remove", r["note"])
        self.assertEqual(len(fake.calls), 2)

    def test_revoke_send_on_behalf_uses_hashtable_remove(self):
        from execution.skills import exo_revoke_mailbox_access as rv
        withsob = {**_MB, "GrantSendOnBehalfTo": ["tech@demodomain.com"]}
        fake = ScriptedEXO([
            ("Get-Mailbox", [withsob]),
            ("Set-Mailbox", {"ok": True}),
            ("Get-Mailbox", [{**_MB, "GrantSendOnBehalfTo": []}]),
        ])
        r = rv.run(_exo_ctx(fake), mailbox="user@demodomain.com",
                   user="tech@demodomain.com", access="send_on_behalf")
        self.assertTrue(r["ok"], r)
        ht = fake.calls[1][1]["GrantSendOnBehalfTo"]
        self.assertEqual(ht["@odata.type"], "#Exchange.GenericHashTable")
        self.assertEqual(ht["Remove"], "tech@demodomain.com")

    def test_revoke_folder_access(self):
        from execution.skills import exo_revoke_folder_access as rf
        held = [{"User": "tech", "AccessRights": ["Editor"]}]
        # call order: Get-Mailbox(mailbox preflight) · Get-Mailbox(target user → identifiers, D-98)
        # · Get-MailboxFolderPermission · Remove · Get-MailboxFolderPermission
        _USER = [{"PrimarySmtpAddress": "tech@demodomain.com", "Alias": "tech",
                  "DisplayName": "Tech User"}]
        fake = ScriptedEXO([
            ("Get-Mailbox", [_MB]),
            ("Get-Mailbox", _USER),
            ("Get-MailboxFolderPermission", held),
            ("Remove-MailboxFolderPermission", {"ok": True}),
            ("Get-MailboxFolderPermission", []),
        ])
        r = rf.run(_exo_ctx(fake), mailbox="user@demodomain.com",
                   user="tech@demodomain.com", folder="calendar")
        self.assertTrue(r["ok"], r)
        rm = next(c for c in fake.calls if c[0] == "Remove-MailboxFolderPermission")
        self.assertEqual(rm[1]["Identity"], "user@demodomain.com:\\Calendar")
        self.assertEqual(r["access_revoked"], "Editor")
        # absent grant → clean no-op
        fake2 = ScriptedEXO([("Get-Mailbox", [_MB]),
                             ("Get-Mailbox", _USER),
                             ("Get-MailboxFolderPermission", [])])
        r2 = rf.run(_exo_ctx(fake2), mailbox="user@demodomain.com",
                    user="tech@demodomain.com", folder="calendar")
        self.assertTrue(r2["ok"])
        self.assertIn("nothing to remove", r2["note"])

    def test_permission_removal_cmdlets_are_writes_not_destructive(self):
        # D-63 line: permission revocation = write; data deletion stays destructive-only.
        from execution.clients.exo import (ALLOWED_CMDLETS, DESTRUCTIVE_CMDLETS,
                                           PARAM_ALLOWLIST)
        for c in ("Remove-MailboxPermission", "Remove-RecipientPermission",
                  "Remove-MailboxFolderPermission"):
            self.assertEqual(ALLOWED_CMDLETS.get(c), "write")
            self.assertIn(c, PARAM_ALLOWLIST)
        self.assertNotIn("Remove-Mailbox", ALLOWED_CMDLETS)
        self.assertIn("Remove-Mailbox", DESTRUCTIVE_CMDLETS)


class D58ComplianceEndpoint(unittest.TestCase):
    """invoke_compliance — separate host, separate token, separate tiny allowlist."""

    def _client(self, sink):
        from execution.clients.exo import EXOClient
        return EXOClient(lambda: "EXO-TOK", "tid-1", "admin@x.com",
                         transport=lambda m, u, headers=None, json_body=None, **_:
                         sink.append((m, u, headers, json_body)) or (200, {"value": []}),
                         compliance_token=lambda: "IPPS-TOK")

    def test_compliance_path_is_separate(self):
        calls = []
        c = self._client(calls)
        # EXO cmdlets don't ride the compliance path and vice versa
        self.assertIn("not in the Security & Compliance allowlist",
                      c.invoke_compliance("Get-Mailbox", {})["error"])
        self.assertIn("not in the EXO allowlist",
                      c.invoke("New-ProtectionAlert", {})["error"])
        self.assertEqual(calls, [])
        c.invoke_compliance("Get-ProtectionAlert", {"Identity": "x"})
        m, u, headers, _b = calls[0]
        self.assertIn("ps.compliance.protection.outlook.com", u)
        self.assertEqual(headers["Authorization"], "Bearer IPPS-TOK")   # not the EXO token

    def test_compliance_param_allowlist(self):
        calls = []
        c = self._client(calls)
        r = c.invoke_compliance("New-ProtectionAlert", {"Name": "x", "Filter": "evil"})
        self.assertIn("not in the allowlist", r["error"])
        self.assertEqual(calls, [])

    def test_no_compliance_token_fails_closed(self):
        from execution.clients.exo import EXOClient
        c = EXOClient(lambda: "T", "tid-1", transport=lambda *a, **k: (200, {}))
        self.assertIn("no Security & Compliance access",
                      c.invoke_compliance("Get-ProtectionAlert", {})["error"])

    def test_compliance_token_minted_from_exo_refresh(self):
        import time as _t
        from execution.core import m365_auth
        cfg = StubCfg({}, tempfile.mkdtemp())
        m365_auth._ipps_cache.clear()
        m365_auth.save_tokens(cfg, "acme", {"refresh_token": "RT-EXO", "tenant_id": "tid-9"},
                              service="exo")
        seen = {}
        orig = m365_auth._form_post
        m365_auth._form_post = lambda url, fields, timeout=30: (
            seen.update(fields) or (200, {"access_token": _jwt(int(_t.time()) + 3600),
                                          "refresh_token": "RT-ROTATED"}))
        try:
            tok = m365_auth.compliance_token(cfg, "acme")
            self.assertTrue(tok)
            self.assertIn("ps.compliance.protection.outlook.com", seen["scope"])
            self.assertEqual(seen["refresh_token"], "RT-EXO")
            # rotation persisted back to the EXO store
            self.assertEqual(m365_auth.load_tokens(cfg, "acme", "exo")["refresh_token"],
                             "RT-ROTATED")
            # cached — a second call doesn't re-post
            seen.clear()
            m365_auth.compliance_token(cfg, "acme")
            self.assertEqual(seen, {})
        finally:
            m365_auth._form_post = orig
            m365_auth._ipps_cache.clear()


class D58AccessReports(unittest.TestCase):
    def test_list_mailboxes_type_filter(self):
        from execution.skills import exo_list_mailboxes
        fake = ScriptedEXO([("Get-Mailbox", [
            {"DisplayName": "Info", "PrimarySmtpAddress": "info@demodomain.com",
             "RecipientTypeDetails": "SharedMailbox"}])])
        r = exo_list_mailboxes.run(_exo_ctx(fake), type="shared")
        self.assertEqual(r["count"], 1)
        self.assertEqual(fake.calls[0][1]["RecipientTypeDetails"], "SharedMailbox")

    def test_mailbox_permissions_filters_system_rows(self):
        from execution.skills import exo_mailbox_permissions as mp
        shared = {**_MB, "RecipientTypeDetails": "SharedMailbox",
                  "GrantSendOnBehalfTo": ["Boss Person"]}
        fake = ScriptedEXO([
            ("Get-Mailbox", [shared]),
            ("Get-MailboxPermission", [
                {"User": "NT AUTHORITY\\SELF", "AccessRights": ["FullAccess"]},
                {"User": "tech@demodomain.com", "AccessRights": ["FullAccess"]}]),
            ("Get-RecipientPermission", [
                {"Trustee": "S-1-5-21-123", "AccessRights": ["SendAs"]},
                {"Trustee": "tech@demodomain.com", "AccessRights": ["SendAs"]}]),
        ])
        r = mp.run(_exo_ctx(fake), identity="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["full_access"], ["tech@demodomain.com"])
        self.assertEqual(r["send_as"], ["tech@demodomain.com"])
        self.assertEqual(r["send_on_behalf"], ["Boss Person"])

    def test_user_mailbox_access_reverse_lookup(self):
        from execution.skills import exo_user_mailbox_access as ua
        own = {**_MB, "PrimarySmtpAddress": "tech@demodomain.com"}
        shared = {**_MB, "PrimarySmtpAddress": "info@demodomain.com",
                  "RecipientTypeDetails": "SharedMailbox"}
        fake = ScriptedEXO([
            ("Get-Mailbox", [own, shared]),
            # own mailbox skipped; only info@ is checked
            ("Get-MailboxPermission", [{"User": "tech@demodomain.com",
                                        "AccessRights": ["FullAccess"]}]),
            ("Get-RecipientPermission", []),
        ])
        r = ua.run(_exo_ctx(fake), user="tech@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["mailboxes"][0]["mailbox"], "info@demodomain.com")
        self.assertEqual(r["mailboxes"][0]["access"], ["full_access"])

    def test_grant_folder_access_add_and_update_paths(self):
        from execution.skills import exo_grant_folder_access as gf
        granted = [{"User": "tech", "AccessRights": ["Reviewer"]}]
        # call order now: Get-Mailbox(mailbox) · Get-Mailbox(target user → identifiers, D-98) ·
        # Get-MailboxFolderPermission · Add-/Set- · Get-MailboxFolderPermission
        _USER = [{"PrimarySmtpAddress": "tech@demodomain.com", "Alias": "tech",
                  "DisplayName": "Tech User"}]
        fake = ScriptedEXO([
            ("Get-Mailbox", [_MB]),
            ("Get-Mailbox", _USER),
            ("Get-MailboxFolderPermission", []),          # no existing entry → Add-
            ("Add-MailboxFolderPermission", {"ok": True}),
            ("Get-MailboxFolderPermission", granted),
        ])
        r = gf.run(_exo_ctx(fake), mailbox="user@demodomain.com",
                   user="tech@demodomain.com", folder="calendar", access="reviewer")
        self.assertTrue(r["ok"], r)
        add = next(c for c in fake.calls if c[0] == "Add-MailboxFolderPermission")
        self.assertEqual(add[1]["Identity"], "user@demodomain.com:\\Calendar")
        self.assertEqual(add[1]["AccessRights"], ["Reviewer"])
        # an existing entry switches to Set- (Add- errors on existing)
        editor = [{"User": "tech", "AccessRights": ["Editor"]}]
        fake2 = ScriptedEXO([
            ("Get-Mailbox", [_MB]),
            ("Get-Mailbox", _USER),
            ("Get-MailboxFolderPermission", granted),     # has Reviewer
            ("Set-MailboxFolderPermission", {"ok": True}),
            ("Get-MailboxFolderPermission", editor),
        ])
        r2 = gf.run(_exo_ctx(fake2), mailbox="user@demodomain.com",
                    user="tech@demodomain.com", folder="calendar", access="editor")
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r2["replaced"], "Reviewer")
        # availability_only is calendar-only
        bad = gf.run(_exo_ctx(None), mailbox="a@demodomain.com", user="b@demodomain.com",
                     folder="contacts", access="availability_only")
        self.assertIn("calendars only", bad["error"])


class D58MailHygiene(unittest.TestCase):
    def test_block_auto_forwarding_creates_the_standard_rule(self):
        from execution.skills import exo_block_auto_forwarding as bf
        fake = ScriptedEXO([
            ("Get-TransportRule", {"error": "HTTP 404 ManagementObjectNotFound"}),
            ("New-TransportRule", {"ok": True}),
            ("Get-TransportRule", [{"Name": "Prevent auto forwarding of email to external "
                                            "domains"}]),
        ])
        r = bf.run(_exo_ctx(fake))
        self.assertTrue(r["ok"], r)
        p = fake.calls[1][1]
        self.assertEqual(p["FromScope"], "InOrganization")
        self.assertEqual(p["SentToScope"], "NotInOrganization")
        self.assertEqual(p["MessageTypeMatches"], "AutoForward")
        self.assertIn("disabled", p["RejectMessageReasonText"])
        # already exists → no-op
        fake2 = ScriptedEXO([("Get-TransportRule", [{"Name": "x"}])])
        self.assertIn("already exists", bf.run(_exo_ctx(fake2))["note"])

    def test_set_junk_filter_single_verifies(self):
        from execution.skills import exo_set_junk_filter as jf
        fake = ScriptedEXO([
            ("Get-MailboxJunkEmailConfiguration", [{"Enabled": True}]),
            ("Set-MailboxJunkEmailConfiguration", {"ok": True}),
            ("Get-MailboxJunkEmailConfiguration", [{"Enabled": False}]),
        ])
        r = jf.run(_exo_ctx(fake), enabled=False, identity="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["junk_filter"], "disabled")
        self.assertIs(fake.calls[1][1]["Enabled"], False)

    def test_set_junk_filter_bulk_counts_and_samples(self):
        from execution.skills import exo_set_junk_filter as jf
        boxes = [{"PrimarySmtpAddress": f"u{i}@demodomain.com"} for i in range(3)]
        fake = ScriptedEXO(
            [("Get-Mailbox", boxes)]
            + [("Set-MailboxJunkEmailConfiguration", {"ok": True})] * 3
            + [("Get-MailboxJunkEmailConfiguration", [{"Enabled": False}])] * 3)
        r = jf.run(_exo_ctx(fake), enabled=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["applied"], 3)
        self.assertEqual(r["sample_verified"], "3/3")
        self.assertEqual(fake.calls[0][1]["RecipientTypeDetails"], "UserMailbox")

    def test_proofpoint_bypass_both_steps(self):
        from execution.skills import exo_setup_proofpoint_bypass as pp
        fake = ScriptedEXO([
            ("Get-TransportRule", {"error": "404 NotFound"}),
            ("New-TransportRule", {"ok": True}),
            ("Get-TransportRule", [{"Name": "x"}]),
            ("Get-InboundConnector", {"error": "404 NotFound"}),
            ("New-InboundConnector", {"ok": True}),
            ("Get-InboundConnector", [{"Name": "y"}]),
        ])
        r = pp.run(_exo_ctx(fake))
        self.assertTrue(r["ok"], r)
        rule = fake.calls[1][1]
        self.assertEqual(rule["SetSCL"], -1)
        self.assertIn("148.163.128.0/19", rule["SenderIPRanges"])
        conn = fake.calls[4][1]
        self.assertEqual(conn["ConnectorType"], "Partner")   # D-66: required for the IP/TLS
        self.assertIs(conn["RequireTls"], True)              # restrictions to actually apply
        self.assertIs(conn["RestrictDomainsToIPAddresses"], True)
        self.assertIn("148.163.159.0/24", conn["SenderIPAddresses"])
        self.assertIn("185.183.31.0/24", conn["SenderIPAddresses"])
        self.assertEqual(conn["SenderDomains"], ["*"])

    def test_forwarding_alert_requires_email_and_verifies(self):
        from execution.skills import exo_add_forwarding_alert as fa
        self.assertIn("not a valid email",
                      fa.run(_exo_ctx(None), notify_email="nope")["error"])
        fake = ScriptedEXO([
            ("Get-ProtectionAlert", {"error": "404 NotFound"}),
            ("New-ProtectionAlert", {"ok": True}),
            ("Get-ProtectionAlert", [{"Name": "Forwarding/redirect rule was created"}]),
        ])
        r = fa.run(_exo_ctx(fake), notify_email="admin@demodomain.com")
        self.assertTrue(r["ok"], r)
        p = fake.calls[1][1]
        self.assertEqual(p["Operation"], ["MailRedirect"])
        self.assertEqual(p["NotifyUser"], ["admin@demodomain.com"])
        self.assertEqual(p["Category"], "ThreatManagement")
        self.assertIn(p["Severity"], ("Low", "Medium", "High"))   # D-66: not "Informational"


class D56PhoneAndMfa(unittest.TestCase):
    def test_phone_normalization(self):
        from execution.skills.m365_add_phone_auth import normalize_phone
        self.assertEqual(normalize_phone("5551234567"), ("+1 5551234567", ""))
        self.assertEqual(normalize_phone("555-123-4567"), ("+1 5551234567", ""))
        self.assertEqual(normalize_phone("(555) 123-4567"), ("+1 5551234567", ""))
        self.assertEqual(normalize_phone("15551234567"), ("+1 5551234567", ""))
        self.assertEqual(normalize_phone("+1 5551234567"), ("+1 5551234567", ""))
        self.assertEqual(normalize_phone("+15551234567"), ("+1 5551234567", ""))
        self.assertEqual(normalize_phone("+44 7911 123456"), ("+44 7911123456", ""))
        self.assertIn("country code", normalize_phone("12345")[1])
        self.assertIn("country code", normalize_phone("+447911123456")[1])  # can't split CC

    def test_set_mfa_verifies(self):
        # D-60: perUserMfaState is beta-only — the skill must call the /beta/ path
        from execution.skills import m365_set_mfa
        states = iter([{"perUserMfaState": "disabled"}, {"perUserMfaState": "enforced"}])
        fake = FakeGraph({"/beta/users/": lambda p: next(states)})
        r = m365_set_mfa.run(_graph_ctx(fake), user="user@demodomain.com", state="enforced")
        self.assertTrue(r["ok"], r)
        method, path, body = fake.writes[0]
        self.assertTrue(path.startswith("/beta/users/"), path)
        self.assertEqual(body, {"perUserMfaState": "enforced"})
        self.assertEqual(r["was"], "disabled")

    def test_graph_client_routes_beta_prefix(self):
        # D-60: '/beta/...' rewrites the URL to graph.microsoft.com/beta — everything else v1.0
        from execution.clients.m365 import M365Client
        calls = []
        c = M365Client(lambda: "TOK",
                       transport=lambda m, u, headers=None, **kw:
                       calls.append(u) or (200, {}))
        c.get("/beta/users/u-1/authentication/requirements")
        c.get("/users/u-1")
        c.patch("/beta/users/u-1/authentication/requirements", {"perUserMfaState": "enforced"})
        self.assertEqual(calls[0],
                         "https://graph.microsoft.com/beta/users/u-1/authentication/requirements")
        self.assertEqual(calls[1], "https://graph.microsoft.com/v1.0/users/u-1")
        self.assertTrue(calls[2].startswith("https://graph.microsoft.com/beta/"))

    def test_beta_prefix_does_not_widen_the_allowlist(self):
        from execution.clients.scopes import is_allowed_read, is_allowed_write
        self.assertTrue(is_allowed_read("m365", "/beta/users/u/authentication/requirements")[0])
        self.assertTrue(is_allowed_write("m365", "/beta/users/u/authentication/requirements",
                                         "PATCH")[0])
        # beta is a version switch, not a surface widening — unlisted paths stay blocked
        self.assertFalse(is_allowed_read("m365", "/beta/applications")[0])
        self.assertFalse(is_allowed_write("m365", "/beta/directoryRoles")[0])

    def test_list_auth_methods_classifies_mfa_vs_not(self):
        from execution.skills import m365_list_auth_methods as am
        methods = {"value": [
            {"@odata.type": "#microsoft.graph.passwordAuthenticationMethod", "id": "p1"},
            {"@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
             "phoneType": "mobile", "phoneNumber": "+1 5551234567"},
            {"@odata.type": "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod",
             "displayName": "Pixel 9"},
            {"@odata.type": "#microsoft.graph.emailAuthenticationMethod",
             "emailAddress": "recovery@demodomain.com"},
        ]}
        fake = FakeGraph({"/users/": methods})
        r = am.run(_graph_ctx(fake), user="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(r["mfa_methods"]), 2)       # phone + Authenticator only
        self.assertEqual(len(r["other_methods"]), 2)     # password + recovery email — NOT MFA
        kinds = {m["method"] for m in r["mfa_methods"]}
        self.assertIn("phone (SMS / voice call)", kinds)
        self.assertIn("Microsoft Authenticator app", kinds)
        self.assertIn("+1 5551234567",
                      next(m["detail"] for m in r["mfa_methods"]
                           if m["method"].startswith("phone")))
        # password-only user → loud "no MFA" summary + pre-provision hint
        fake2 = FakeGraph({"/users/": {"value": [
            {"@odata.type": "#microsoft.graph.passwordAuthenticationMethod", "id": "p1"}]}})
        r2 = am.run(_graph_ctx(fake2), user="user@demodomain.com")
        self.assertIn("NO MFA", r2["summary"])
        self.assertIn("m365_add_phone_auth", r2["note"])

    def test_mfa_status_sweep_buckets(self):
        from execution.skills import m365_mfa_status
        per_user = {"a@demodomain.com": "enforced", "b@demodomain.com": "disabled",
                    "c@demodomain.com": "enabled"}

        class Fake(FakeGraph):
            def get(self, path, params=None):
                if path == "/users":
                    return {"value": [{"userPrincipalName": u} for u in per_user]}
                for u, s in per_user.items():
                    if u in path:
                        return {"perUserMfaState": s}
                return {"error": f"unexpected {path}"}
        r = m365_mfa_status.run(_graph_ctx(Fake({})))
        self.assertEqual(r["summary"], {"enforced": 1, "enabled": 1, "disabled": 1})
        self.assertEqual(r["mfa_disabled"], ["b@demodomain.com"])


class D56EntraGroups(unittest.TestCase):
    def test_create_group_shapes(self):
        from execution.skills import m365_create_group
        fake = FakeGraph({"/groups": lambda p: {"value": []} if p == "/groups"
                          else {"id": "g-1", "displayName": "Sales Dynamic"}})
        r = m365_create_group.run(_graph_ctx(fake), name="Sales Dynamic", kind="dynamic",
                                  membership_rule='(user.department -eq "Sales")')
        self.assertTrue(r["ok"], r)
        body = fake.writes[0][2]
        self.assertEqual(body["groupTypes"], ["DynamicMembership"])
        self.assertEqual(body["membershipRuleProcessingState"], "On")
        self.assertTrue(body["securityEnabled"])
        # dynamic requires a rule; security refuses a rule
        self.assertIn("membership_rule",
                      m365_create_group.run(_graph_ctx(fake), name="X",
                                            kind="dynamic")["error"])
        self.assertIn("dynamic",
                      m365_create_group.run(_graph_ctx(fake), name="X", kind="security",
                                            membership_rule="(x)")["error"])

    def test_add_member_refuses_dynamic_groups(self):
        from execution.skills import m365_add_security_group_member
        fake = FakeGraph({"/groups": {"value": [
            {"id": "g-1", "displayName": "Dyn", "groupTypes": ["DynamicMembership"],
             "membershipRule": '(user.department -eq "Sales")'}]}})
        r = m365_add_security_group_member.run(_graph_ctx(fake), group="Dyn",
                                               member="user@demodomain.com")
        self.assertFalse(r["ok"])
        self.assertIn("DYNAMIC", r["error"])
        self.assertEqual(fake.writes, [])

    def test_add_member_verifies(self):
        from execution.skills import m365_add_security_group_member
        member_lists = iter([{"value": []}, {"value": [{"id": "u-1"}]}])

        class Fake(FakeGraph):
            def get(self, path, params=None):
                if path.endswith("/members"):
                    return next(member_lists)
                if path == "/groups":
                    return {"value": [{"id": "g-1", "displayName": "Helpdesk",
                                       "groupTypes": []}]}
                if path.startswith("/users/"):
                    return {"id": "u-1", "userPrincipalName": "user@demodomain.com"}
                return {"error": f"unexpected {path}"}
        fake = Fake({})
        r = m365_add_security_group_member.run(_graph_ctx(fake), group="Helpdesk",
                                               member="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        m, path, body = fake.writes[0]
        self.assertEqual(path, "/groups/g-1/members/$ref")
        self.assertIn("directoryObjects/u-1", body["@odata.id"])


class D56ExoCreates(unittest.TestCase):
    def test_distribution_group_full_flow(self):
        from execution.skills import exo_create_distribution_group as dg
        fake = ScriptedEXO([
            ("Get-DistributionGroup", {"error": "HTTP 404 ManagementObjectNotFound"}),
            ("New-DistributionGroup", {"ok": True}),
            ("Get-DistributionGroup", [{"PrimarySmtpAddress": "team@demodomain.com"}]),
        ])
        r = dg.run(_exo_ctx(fake), email="team@demodomain.com",
                   members=["a@demodomain.com"])
        self.assertTrue(r["ok"], r)
        params = fake.calls[1][1]
        self.assertEqual(params["Type"], "Distribution")
        self.assertEqual(params["Members"], ["a@demodomain.com"])
        # duplicate refused
        fake2 = ScriptedEXO([("Get-DistributionGroup",
                              [{"PrimarySmtpAddress": "team@demodomain.com"}])])
        self.assertIn("already exists",
                      dg.run(_exo_ctx(fake2), email="team@demodomain.com")["error"])

    def test_contact_full_flow(self):
        from execution.skills import exo_create_contact as ct
        fake = ScriptedEXO([
            ("Get-MailContact", {"error": "HTTP 404 ManagementObjectNotFound"}),
            ("New-MailContact", {"ok": True}),
            ("Get-MailContact", [{"ExternalEmailAddress": "jane@vendorco.com"}]),
        ])
        r = ct.run(_exo_ctx(fake), name="Jane Vendor", email="jane@vendorco.com",
                   first_name="Jane", last_name="Vendor")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.calls[1][1]["ExternalEmailAddress"], "jane@vendorco.com")


class D56Autopilot(unittest.TestCase):
    def test_add_device_validates_hash(self):
        from execution.skills import m365_add_autopilot_device as ap
        r = ap.run(_graph_ctx(FakeGraph({})), serial="S1", hardware_hash="short")
        self.assertIn("hardware hash", r["error"])
        r2 = ap.run(_graph_ctx(FakeGraph({})), serial="S1", hardware_hash="@@@" * 100)
        self.assertIn("base64", r2["error"])

    def test_add_device_reports_submitted_never_done(self):
        import base64
        from execution.skills import m365_add_autopilot_device as ap
        good_hash = base64.b64encode(b"x" * 900).decode()
        fake = FakeGraph({})
        r = ap.run(_graph_ctx(fake), serial="S1", hardware_hash=good_hash,
                   group_tag="MSP AI", assigned_user="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertIn("SUBMITTED", r["note"])              # async — never claims imported
        body = fake.writes[0][2]
        self.assertEqual(body["serialNumber"], "S1")
        self.assertEqual(body["groupTag"], "MSP AI")
        self.assertEqual(body["hardwareIdentifier"], good_hash)


class D57Offboard(unittest.TestCase):
    """The disable-account composite — reordered + hybrid-aware + lists groups (D-105)."""

    class RoutedGraph:
        def __init__(self, hybrid=False, memberof=None):
            self.writes = []
            self.licenses = [{"skuId": "s-1"}]
            self.hybrid = hybrid
            self.memberof = memberof or []

        def get(self, path, params=None):
            sel = (params or {}).get("$select", "")
            if path == "/users/user@demodomain.com":
                return {"id": "u-1", "userPrincipalName": "user@demodomain.com",
                        "onPremisesSyncEnabled": self.hybrid}
            if path == "/users/u-1/memberOf/microsoft.graph.group":
                return {"value": self.memberof}
            if path == "/users/u-1" and "assignedLicenses" in sel:
                return {"assignedLicenses": self.licenses}
            if path == "/users/u-1" and "displayName" in sel:
                return {"displayName": "User Person"}
            return {"error": f"unexpected GET {path}"}

        def post(self, path, body=None):
            self.writes.append(("POST", path, body))
            if path.endswith("/assignLicense"):
                self.licenses = []
            return {"ok": True}

        def patch(self, path, body=None):
            self.writes.append(("PATCH", path, body))
            return {"ok": True}

    def _exo_script(self, size="1.2 GB (1,288,490,189 bytes)"):
        # EXO sequence for the standard mailbox steps (rename removed — now exo_rename_smtp, D-105)
        shared = {**_MB, "RecipientTypeDetails": "SharedMailbox"}
        hidden = {**shared, "HiddenFromAddressListsEnabled": True}
        return ScriptedEXO([
            ("Get-Mailbox", [_MB]),                          # initial preflight
            ("Set-Mailbox", {"ok": True}),                   # convert (Set + poll, D-104b)
            ("Get-Mailbox", [shared]),                       # convert: verified (first poll read)
            ("Get-MailboxStatistics", [{"TotalItemSize": size}]),
            ("Get-Mailbox", [shared]),                       # hide: before
            ("Set-Mailbox", {"ok": True}),
            ("Get-Mailbox", [hidden]),                       # hide: verified
        ])

    def _ctx(self, graph, exo):
        from execution.core.context import ToolContext
        return ToolContext(tenant_id="acme", actor="t",
                           client_factory=lambda i, t: graph if i == "m365" else exo)

    def test_full_offboard(self):
        from execution.skills import m365_offboard_user as ob
        graph, exo = self.RoutedGraph(), self._exo_script()
        r = ob.run(self._ctx(graph, exo), user="user@demodomain.com", list_groups=False, list_mailbox_access=False)
        self.assertTrue(r["ok"], r)
        s = r["steps"]
        for step in ("block_signin", "sign_out_devices", "reset_password",
                     "convert_to_shared", "hide_from_gal", "prefix_display_name"):
            self.assertIn("done", str(s[step]), (step, s[step]))
        self.assertIn("all licenses removed", s["remove_licenses"])
        self.assertNotIn("rename_smtp", s)                   # rename is now a separate tool
        # the scrambled password is never disclosed
        self.assertNotIn("password", str(r).lower().replace("reset_password", ""))
        pw_patch = next(b for m, p, b in graph.writes
                        if m == "PATCH" and b and "passwordProfile" in b)
        self.assertTrue(pw_patch["passwordProfile"]["password"])
        disable_patch = next(b for m, p, b in graph.writes if b == {"accountEnabled": False})
        self.assertIsNotNone(disable_patch)
        name_patch = next(b for m, p, b in graph.writes
                          if b and b.get("displayName") == "zzz_User Person")
        self.assertIsNotNone(name_patch)

    def test_hybrid_skips_ad_mastered_steps(self):
        # D-105: in a hybrid tenant, password reset + sign-in block are mastered in on-prem AD —
        # skip them (with guidance), don't fail; sign-out (a cloud op) still runs.
        from execution.skills import m365_offboard_user as ob
        graph = self.RoutedGraph(hybrid=True)
        r = ob.run(self._ctx(graph, ScriptedEXO([])), user="user@demodomain.com",
                   convert_to_shared=False, remove_licenses=False, hide_from_gal=False,
                   prefix_display_name=False, list_groups=False, list_mailbox_access=False)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["hybrid"])
        self.assertIn("directory-synced", r["steps"]["reset_password"])
        self.assertIn("directory-synced", r["steps"]["block_signin"])
        self.assertIn("done", r["steps"]["sign_out_devices"])
        self.assertFalse(any(b == {"accountEnabled": False} for _m, _p, b in graph.writes))
        self.assertFalse(any("passwordProfile" in (b or {}) for _m, _p, b in graph.writes))

    def test_lists_groups_for_owner_without_removing(self):
        # D-105: offboard LISTS distribution (EXO) + security (Graph) groups; removes nothing.
        from execution.skills import m365_offboard_user as ob
        graph = self.RoutedGraph(memberof=[
            {"id": "g-sec", "displayName": "VPN Users", "securityEnabled": True,
             "mailEnabled": False, "groupTypes": []},
            {"id": "g-m365", "displayName": "Marketing", "mailEnabled": True,
             "groupTypes": ["Unified"]},                     # m365 → excluded from security list
        ])
        exo = ScriptedEXO([
            ("Get-Mailbox", [{"PrimarySmtpAddress": "user@demodomain.com",
                              "DistinguishedName": "CN=User,DC=x"}]),
            ("Get-Recipient", [{"DisplayName": "Sales DL", "PrimarySmtpAddress": "sales@x",
                                "RecipientTypeDetails": "MailUniversalDistributionGroup"}]),
        ])
        r = ob.run(self._ctx(graph, exo), user="user@demodomain.com",
                   sign_out_devices=False, reset_password=False, block_signin=False,
                   convert_to_shared=False, remove_licenses=False, hide_from_gal=False,
                   prefix_display_name=False, list_groups=True, list_mailbox_access=False)
        self.assertTrue(r["ok"], r)
        gc = r["group_cleanup"]
        self.assertEqual([x["name"] for x in gc["distribution_groups"]], ["Sales DL"])
        self.assertEqual([x["name"] for x in gc["security_groups"]], ["VPN Users"])
        self.assertIn("NOT AUTOMATIC", gc["instruction"])
        self.assertEqual(graph.writes, [])                   # nothing removed or changed

    def test_lists_mailbox_access_for_owner_without_revoking(self):
        # D-106 follow-up: offboard surfaces the mailboxes the user has Full Access / Send-As on
        # (the mirror of what onboard grants) — read-only, never revokes.
        from execution.skills import m365_offboard_user as ob
        exo = ScriptedEXO([
            ("Get-Mailbox", [                                # the access sweep
                {"PrimarySmtpAddress": "thealtiers@x.com", "DisplayName": "Shared",
                 "RecipientTypeDetails": "SharedMailbox", "GrantSendOnBehalfTo": []},
                {"PrimarySmtpAddress": "user@demodomain.com", "DisplayName": "Self",
                 "RecipientTypeDetails": "UserMailbox", "GrantSendOnBehalfTo": []}]),
            ("Get-MailboxPermission", [{"AccessRights": ["FullAccess"]}]),     # full access
            ("Get-RecipientPermission", [{"Trustee": "user@demodomain.com"}]),  # send-as
        ])
        graph = self.RoutedGraph()
        r = ob.run(self._ctx(graph, exo), user="user@demodomain.com",
                   sign_out_devices=False, reset_password=False, block_signin=False,
                   convert_to_shared=False, remove_licenses=False, hide_from_gal=False,
                   prefix_display_name=False, list_groups=False, list_mailbox_access=True)
        self.assertTrue(r["ok"], r)
        mac = r["mailbox_access_cleanup"]
        self.assertEqual([m["mailbox"] for m in mac["mailboxes"]], ["thealtiers@x.com"])
        self.assertEqual(sorted(mac["mailboxes"][0]["access"]), ["full_access", "send_as"])
        self.assertIn("NOT AUTOMATIC", mac["instruction"])
        self.assertEqual(graph.writes, [])                   # nothing revoked or changed

    def test_big_mailbox_keeps_licenses_with_warning(self):
        from execution.skills import m365_offboard_user as ob
        graph = self.RoutedGraph()
        exo = self._exo_script(size="61.4 GB (65,927,544,832 bytes)")
        r = ob.run(self._ctx(graph, exo), user="user@demodomain.com", list_groups=False, list_mailbox_access=False)
        self.assertTrue(r["ok"], r)                          # a skip-with-warning is still ok
        self.assertIn("SKIPPED", r["steps"]["remove_licenses"])
        self.assertTrue(any("50 GB" in w for w in r["warnings"]))
        self.assertEqual(graph.licenses, [{"skuId": "s-1"}])  # licenses untouched
        self.assertFalse(any(p.endswith("/assignLicense") for _m, p, _b in graph.writes))

    def test_flags_off_skip_steps(self):
        from execution.skills import m365_offboard_user as ob
        graph = self.RoutedGraph()
        exo = ScriptedEXO([])                                # no EXO calls expected
        r = ob.run(self._ctx(graph, exo), user="user@demodomain.com",
                   convert_to_shared=False, remove_licenses=False, hide_from_gal=False,
                   prefix_display_name=False, reset_password=False, list_groups=False, list_mailbox_access=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(r["steps"]), ["block_signin", "sign_out_devices"])
        self.assertEqual(exo.calls, [])


class D105GroupListAndRename(unittest.TestCase):
    """D-105 — authoritative DL membership read + the split-out rename tool."""

    def test_user_distribution_groups_lists_and_classifies(self):
        from execution.skills import exo_user_distribution_groups as dl
        exo = ScriptedEXO([
            ("Get-Mailbox", [{"PrimarySmtpAddress": "u@x.com",
                              "DistinguishedName": "CN=U,DC=x"}]),
            ("Get-Recipient", [
                {"DisplayName": "Sales", "PrimarySmtpAddress": "sales@x.com",
                 "RecipientTypeDetails": "MailUniversalDistributionGroup"},
                {"DisplayName": "Dyn", "PrimarySmtpAddress": "dyn@x.com",
                 "RecipientTypeDetails": "DynamicDistributionGroup"}]),
        ])
        r = dl.run(_exo_ctx(exo), user="u@x.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["count"], 2)
        by = {g["name"]: g for g in r["groups"]}
        self.assertEqual(by["Sales"]["remove_with"], "exo_remove_group_member")
        self.assertFalse(by["Dyn"]["removable"])             # dynamic = not manually removable
        # the OPATH filter targets the user's DN
        self.assertIn("Members -eq 'CN=U,DC=x'", fake_filter := exo.calls[1][1]["Filter"])

    def test_rename_smtp_renames_and_drops_old_address(self):
        from execution.skills import exo_rename_smtp as rn
        renamed = {"PrimarySmtpAddress": "zzz_user@demodomain.com",
                   "EmailAddresses": ["SMTP:zzz_user@demodomain.com"]}
        exo = ScriptedEXO([
            ("Get-Mailbox", [_MB]),                          # preflight
            ("Set-Mailbox", {"ok": True}),                   # rename primary + UPN
            ("Set-Mailbox", {"ok": True}),                   # drop old alias
            ("Get-Mailbox", [renamed]),                      # verify old gone
        ])
        r = rn.run(_exo_ctx(exo), user="user@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["mailbox"], "zzz_user@demodomain.com")

    def test_rename_smtp_noop_when_already_prefixed(self):
        from execution.skills import exo_rename_smtp as rn
        exo = ScriptedEXO([("Get-Mailbox",
                            [{"PrimarySmtpAddress": "zzz_user@demodomain.com"}])])
        r = rn.run(_exo_ctx(exo), user="zzz_user@demodomain.com")
        self.assertTrue(r["ok"])
        self.assertIn("already renamed", r["note"])


class D106Onboard(unittest.TestCase):
    """D-106 — onboarding: license wait, contact set, and the composite orchestration."""

    def _poll0(self):
        os.environ["MSPAI_LICENSE_POLL_SECONDS"] = "0"
        os.environ["MSPAI_VERIFY_DELAY"] = "0"
        self.addCleanup(lambda: os.environ.pop("MSPAI_LICENSE_POLL_SECONDS", None))
        self.addCleanup(lambda: os.environ.pop("MSPAI_VERIFY_DELAY", None))

    def test_wait_for_license_available_now(self):
        self._poll0()
        from execution.skills import m365_wait_for_license as w
        fake = FakeGraph({"/subscribedSkus": {"value": [
            {"skuPartNumber": "SPE_F1", "skuId": "s1",
             "prepaidUnits": {"enabled": 5}, "consumedUnits": 2}]}})
        r = w.run(_graph_ctx(fake), license="SPE_F1")
        self.assertTrue(r["available"])
        self.assertEqual(r["available"], True)

    def test_wait_for_license_no_seat_then_gives_up(self):
        self._poll0()
        from execution.skills import m365_wait_for_license as w
        fake = FakeGraph({"/subscribedSkus": {"value": [
            {"skuPartNumber": "SPE_F1", "skuId": "s1",
             "prepaidUnits": {"enabled": 2}, "consumedUnits": 2}]}})
        r = w.run(_graph_ctx(fake), license="SPE_F1", minutes=0.001)
        self.assertFalse(r["available"])
        self.assertTrue(r["found"])

    def test_wait_for_license_appears_after_polling(self):
        self._poll0()
        from execution.skills import m365_wait_for_license as w
        seq = iter([{"value": []},
                    {"value": [{"skuPartNumber": "SPE_F1", "skuId": "s1",
                                "prepaidUnits": {"enabled": 1}, "consumedUnits": 0}]}])
        fake = FakeGraph({"/subscribedSkus": lambda p: next(seq)})
        r = w.run(_graph_ctx(fake), license="SPE_F1", minutes=1)
        self.assertTrue(r["available"])

    def test_set_user_contact_sets_and_verifies(self):
        self._poll0()
        from execution.skills import m365_set_user_contact as sc

        class G(FakeGraph):
            def get(self, path, params=None):
                sel = (params or {}).get("$select", "")
                if path == "/users/u@x.com" and "jobTitle" not in sel:
                    return {"id": "u-1", "onPremisesSyncEnabled": False}
                if path == "/users/u-1":
                    return {"id": "u-1", "jobTitle": "Tech", "department": "IT"}
                return {"error": f"unexpected {path}"}
        g = G({})
        r = sc.run(_graph_ctx(g), user="u@x.com", job_title="Tech", department="IT")
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(r["updated"]), ["department", "jobTitle"])
        patch = next(b for m, p, b in g.writes if m == "PATCH")
        self.assertEqual(patch, {"jobTitle": "Tech", "department": "IT"})

    def test_set_user_contact_refuses_on_hybrid(self):
        from execution.skills import m365_set_user_contact as sc
        g = FakeGraph({"/users/u@x.com": {"id": "u-1", "onPremisesSyncEnabled": True}})
        r = sc.run(_graph_ctx(g), user="u@x.com", job_title="Tech")
        self.assertFalse(r["ok"])
        self.assertTrue(r["on_prem_synced"])
        self.assertEqual(g.writes, [])

    def _patch_substeps(self, calls):
        import execution.skills.exo_add_group_member as dg
        import execution.skills.exo_grant_mailbox_access as gma
        import execution.skills.m365_add_phone_auth as ap
        import execution.skills.m365_add_security_group_member as sg
        import execution.skills.m365_assign_license as al
        import execution.skills.m365_set_mfa as mfa
        import execution.skills.m365_set_user_contact as sc
        mods = {"al": al, "sc": sc, "sg": sg, "dg": dg, "gma": gma, "ap": ap, "mfa": mfa}
        for m in mods.values():
            self.addCleanup(setattr, m, "run", m.run)
        al.run = lambda ctx, **k: (calls.append(("license", k.get("license"),
                                                 k.get("disabled_apps"))), {"ok": True})[1]
        sc.run = lambda ctx, **k: (calls.append(("contact", sorted(x for x in k if x != "user"))),
                                   {"ok": True})[1]
        sg.run = lambda ctx, **k: (calls.append(("secgrp", k.get("group"))), {"ok": True})[1]
        dg.run = lambda ctx, **k: (calls.append(("dl", k.get("group"))), {"ok": True})[1]
        gma.run = lambda ctx, **k: (calls.append(("mbx", k.get("mailbox"), k.get("access"))),
                                    {"ok": True})[1]
        ap.run = lambda ctx, **k: (calls.append(("phone", k.get("phone"))), {"ok": True})[1]
        mfa.run = lambda ctx, **k: (calls.append(("mfa", k.get("state"))), {"ok": True})[1]
        return mods

    def test_onboard_runs_all_steps_in_order(self):
        from execution.skills import m365_onboard_user as ob
        calls: list = []
        self._patch_substeps(calls)
        graph = FakeGraph({"/users/jdoe@x.com": {"id": "u-1", "onPremisesSyncEnabled": False}})
        r = ob.run(_graph_ctx(graph), user="jdoe@x.com",
                   licenses=[{"sku": "SPE_F1", "disabled_apps": ["TEAMS1"]}],
                   contact={"job_title": "Tech"}, security_groups=["VPN"],
                   distribution_groups=["Sales"], mailboxes=["info@x.com"],
                   mfa_phone="+15551234567", enforce_mfa=True)
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["hybrid"])
        self.assertEqual([c[0] for c in calls],
                         ["license", "contact", "secgrp", "dl", "mbx", "mbx", "phone", "mfa"])
        self.assertEqual([c for c in calls if c[0] == "mbx"],
                         [("mbx", "info@x.com", "full_access"),
                          ("mbx", "info@x.com", "send_as")])
        self.assertEqual(calls[-1], ("mfa", "enforced"))
        self.assertEqual(calls[0], ("license", "SPE_F1", ["TEAMS1"]))

    def test_onboard_creates_user_when_non_hybrid_and_missing(self):
        import execution.skills.m365_create_user as cu
        from execution.skills import m365_onboard_user as ob
        calls: list = []
        self._patch_substeps(calls)
        self.addCleanup(setattr, cu, "run", cu.run)
        created = {}
        cu.run = lambda ctx, **k: (created.update(k), {"ok": True})[1]
        graph = FakeGraph({
            "/organization": {"value": [{"onPremisesSyncEnabled": False}]},
            # /users/new@x.com falls through to the default unexpected-GET error => "not found"
        })
        r = ob.run(_graph_ctx(graph), user="new@x.com", create_first_name="New",
                   create_last_name="Hire", enforce_mfa=False, licenses=[{"sku": "SPE_E3"}])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["steps"]["create_user"], "done")
        self.assertEqual(created["email"], "new@x.com")
        self.assertEqual((created["first_name"], created["last_name"]), ("New", "Hire"))

    def test_onboard_hybrid_missing_user_refuses_create(self):
        from execution.skills import m365_onboard_user as ob
        graph = FakeGraph({"/organization": {"value": [{"onPremisesSyncEnabled": True}]}})
        r = ob.run(_graph_ctx(graph), user="ghost@x.com", create_first_name="G",
                   create_last_name="H")
        self.assertFalse(r["ok"])
        self.assertTrue(r["tenant_hybrid"])


if __name__ == "__main__":
    unittest.main()
