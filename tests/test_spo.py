"""SharePoint Online admin (site-collection-admin) + OneDrive grant tool tests (D-89)."""
import unittest

from execution.clients.spo import SPOClient, login_claim
from execution.core import m365_auth
from execution.core.credentials import MissingCredential
from execution.skills import m365_grant_onedrive_access as grant


class FakeTransport:
    """Captures the last CSOM call and returns a canned (status, data)."""
    def __init__(self, data, status=200):
        self.data = data
        self.status = status
        self.calls = []

    def __call__(self, method, url, headers=None, body=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.status, self.data


class HostDerivation(unittest.TestCase):
    def test_admin_and_my_hosts_from_root(self):
        h = m365_auth._hosts_from_root_weburl("https://contoso.sharepoint.com")
        self.assertEqual(h["root_host"], "contoso.sharepoint.com")
        self.assertEqual(h["admin_host"], "contoso-admin.sharepoint.com")
        self.assertEqual(h["my_host"], "contoso-my.sharepoint.com")

    def test_trailing_path_ignored(self):
        h = m365_auth._hosts_from_root_weburl("https://fabrikam.sharepoint.com/")
        self.assertEqual(h["admin_host"], "fabrikam-admin.sharepoint.com")

    def test_non_sharepoint_host_rejected(self):
        with self.assertRaises(MissingCredential):
            m365_auth._hosts_from_root_weburl("https://example.com")

    def test_spo_scope_uses_admin_host(self):
        self.assertEqual(
            m365_auth._spo_scope("contoso-admin.sharepoint.com"),
            "https://contoso-admin.sharepoint.com/.default offline_access openid profile")


class LoginClaim(unittest.TestCase):
    def test_member_claim(self):
        self.assertEqual(login_claim("jo@x.com"), "i:0#.f|membership|jo@x.com")

    def test_already_a_claim_is_left_alone(self):
        self.assertEqual(login_claim("i:0#.f|membership|jo@x.com"), "i:0#.f|membership|jo@x.com")


class SetSiteAdmin(unittest.TestCase):
    def _client(self, transport):
        return SPOClient(lambda: "TOK", "contoso-admin.sharepoint.com",
                         my_host="contoso-my.sharepoint.com", transport=transport)

    def test_success_posts_csom_to_admin_host(self):
        t = FakeTransport([{"ErrorInfo": None, "TraceCorrelationId": "abc"}])
        r = self._client(t).set_site_admin(
            "https://contoso-my.sharepoint.com/personal/jo_x_com",
            login_claim("mgr@x.com"))
        self.assertTrue(r["ok"])
        call = t.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"],
                         "https://contoso-admin.sharepoint.com/_vti_bin/client.svc/ProcessQuery")
        self.assertIn("Bearer TOK", call["headers"]["Authorization"])
        self.assertIn("text/xml", call["headers"]["Content-Type"])
        # the CSOM body names the method, the site, the user claim, and active=true
        self.assertIn("SetSiteAdmin", call["body"])
        self.assertIn("personal/jo_x_com", call["body"])
        self.assertIn("i:0#.f|membership|mgr@x.com", call["body"])
        self.assertIn('<Parameter Type="Boolean">true</Parameter>', call["body"])

    def test_csom_errorinfo_is_surfaced_as_failure(self):
        t = FakeTransport([{"ErrorInfo": {"ErrorMessage": "Cannot find user."}}])
        r = self._client(t).set_site_admin("https://contoso-my.sharepoint.com/personal/jo_x_com",
                                           login_claim("nope@x.com"))
        self.assertNotIn("ok", r)
        self.assertIn("Cannot find user.", r["error"])

    def test_rejects_non_https_site(self):
        t = FakeTransport([])
        r = self._client(t).set_site_admin("not-a-url", login_claim("mgr@x.com"))
        self.assertIn("error", r)
        self.assertEqual(t.calls, [])             # never hit the wire


class SiteUrlFromDrive(unittest.TestCase):
    def test_strips_documents_library(self):
        self.assertEqual(
            grant._site_url_from_drive(
                "https://contoso-my.sharepoint.com/personal/jo_x_com/Documents"),
            "https://contoso-my.sharepoint.com/personal/jo_x_com")

    def test_no_documents_suffix(self):
        self.assertEqual(
            grant._site_url_from_drive("https://contoso-my.sharepoint.com/personal/jo_x_com"),
            "https://contoso-my.sharepoint.com/personal/jo_x_com")


class GrantTool(unittest.TestCase):
    """Drive the tool end-to-end with a fake ctx (Graph reads + SPO client stubbed)."""

    class Ctx:
        def __init__(self, drive_web_url, spo_result, users=("jo@x.com", "mgr@x.com"),
                     licensed=True, enabled=False, site_url=None):
            self.tenant_id = "acme"
            self._drive = drive_web_url
            # the canonical site URL Graph returns in sharepointIds.siteUrl; None => omit it so the
            # tool falls back to stripping the webUrl.
            self._site_url = site_url
            self._spo_result = spo_result
            self._users = set(users)
            self._licensed = licensed
            self._enabled = enabled
            self.spo_calls = []

        # scoped_read goes through ctx.client("m365").get(...)
        def client(self, vendor):
            if vendor == "m365":
                return self._Graph(self)
            if vendor == "spo":
                return self._Spo(self)
            raise AssertionError(vendor)

        class _Graph:
            def __init__(self, outer): self.o = outer
            def get(self, path, params=None):
                sel = (params or {}).get("$select", "")
                if path.startswith("/users/") and path.count("/") == 2:
                    ident = path.split("/")[2]
                    # /users/<id>?$select=accountEnabled,assignedLicenses  (license check)
                    if "accountEnabled" in sel:
                        return {"accountEnabled": self.o._enabled,
                                "assignedLicenses": ([{"skuId": "x"}] if self.o._licensed else [])}
                    # /users/<upn>?$select=id,userPrincipalName  (resolve_user_id)
                    if ident in self.o._users:
                        return {"id": "id-" + ident, "userPrincipalName": ident}
                    return {"error": {"message": f"no user {ident}"}}
                # /users/<id>/drive/root?$select=webUrl,sharepointIds
                if path.endswith("/drive/root"):
                    if not self.o._drive and not self.o._site_url:
                        return {}                          # no provisioned OneDrive
                    out = {"webUrl": self.o._drive}
                    if self.o._site_url is not None:
                        out["sharepointIds"] = {"siteUrl": self.o._site_url}
                    return out
                raise AssertionError(path)

        class _Spo:
            def __init__(self, outer): self.o = outer
            def set_site_admin(self, site_url, login_name, is_admin=True):
                self.o.spo_calls.append((site_url, login_name, is_admin))
                return self.o._spo_result

    def test_prefers_sharepointids_siteurl(self):
        # Canonical: Graph returns the site URL directly (possibly with a GUID suffix) — use it
        # verbatim rather than string-stripping the library webUrl.
        ctx = self.Ctx("https://contoso-my.sharepoint.com/personal/jo_x_com2/Documents",
                       {"ok": True},
                       site_url="https://contoso-my.sharepoint.com/personal/jo_x_com2")
        r = grant.run(ctx, former_employee="jo@x.com", grant_to="mgr@x.com")
        self.assertTrue(r["ok"])
        self.assertEqual(r["onedrive_url"],
                         "https://contoso-my.sharepoint.com/personal/jo_x_com2")
        self.assertEqual(ctx.spo_calls[0][0],
                         "https://contoso-my.sharepoint.com/personal/jo_x_com2")

    def test_happy_path_falls_back_to_weburl_when_no_sharepointids(self):
        ctx = self.Ctx("https://contoso-my.sharepoint.com/personal/jo_x_com/Documents",
                       {"ok": True})                     # site_url=None → fallback path
        r = grant.run(ctx, former_employee="jo@x.com", grant_to="mgr@x.com")
        self.assertTrue(r["ok"])
        self.assertEqual(r["onedrive_url"],
                         "https://contoso-my.sharepoint.com/personal/jo_x_com")
        self.assertEqual(ctx.spo_calls,
                         [("https://contoso-my.sharepoint.com/personal/jo_x_com",
                           "i:0#.f|membership|mgr@x.com", True)])

    def test_unlicensed_former_account_is_flagged_time_boxed(self):
        ctx = self.Ctx("https://contoso-my.sharepoint.com/personal/jo_x_com/Documents",
                       {"ok": True}, licensed=False)
        r = grant.run(ctx, former_employee="jo@x.com", grant_to="mgr@x.com")
        self.assertTrue(r["ok"])                  # the grant still works without a license
        av = r["availability"]
        self.assertFalse(av["former_account_licensed"])
        self.assertTrue(av["time_boxed"])
        self.assertTrue(av["retention_timeline"])
        self.assertIn("time-boxed", r["note"])

    def test_licensed_former_account_not_time_boxed(self):
        ctx = self.Ctx("https://contoso-my.sharepoint.com/personal/jo_x_com/Documents",
                       {"ok": True}, licensed=True)
        r = grant.run(ctx, former_employee="jo@x.com", grant_to="mgr@x.com")
        self.assertTrue(r["availability"]["former_account_licensed"])
        self.assertFalse(r["availability"]["time_boxed"])
        self.assertNotIn("time-boxed", r["note"])

    def test_unknown_former_employee_errors_before_sharepoint(self):
        ctx = self.Ctx("https://x", {"ok": True}, users=("mgr@x.com",))
        r = grant.run(ctx, former_employee="ghost@x.com", grant_to="mgr@x.com")
        self.assertFalse(r.get("ok"))
        self.assertEqual(ctx.spo_calls, [])

    def test_no_onedrive_reports_clearly(self):
        ctx = self.Ctx("", {"ok": True})          # /drive returns no webUrl
        r = grant.run(ctx, former_employee="jo@x.com", grant_to="mgr@x.com")
        self.assertFalse(r.get("ok"))
        self.assertIn("no OneDrive", r["error"])
        self.assertEqual(ctx.spo_calls, [])

    def test_not_a_upn_rejected(self):
        ctx = self.Ctx("https://x", {"ok": True})
        r = grant.run(ctx, former_employee="jo", grant_to="mgr@x.com")
        self.assertFalse(r.get("ok"))

    def test_sharepoint_refusal_surfaced(self):
        ctx = self.Ctx("https://contoso-my.sharepoint.com/personal/jo_x_com/Documents",
                       {"error": "SharePoint refused: nope"})
        r = grant.run(ctx, former_employee="jo@x.com", grant_to="mgr@x.com")
        self.assertFalse(r.get("ok"))
        self.assertIn("nope", r["error"])


if __name__ == "__main__":
    unittest.main()
