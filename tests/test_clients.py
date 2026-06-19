"""Client tests — pagination, slimming, auth flow, JWT — all with injected transports."""
import unittest

from execution.clients._http import encode_jwt_hs256
from execution.clients.cylance import CylanceClient
from execution.clients.huntress import HuntressClient
from execution.clients.kaseya import KaseyaClient


class JWT(unittest.TestCase):
    def test_hs256_known_vector(self):
        # Canonical jwt.io example.
        token = encode_jwt_hs256(
            {"sub": "1234567890", "name": "John Doe", "iat": 1516239022},
            "your-256-bit-secret",
        )
        self.assertEqual(
            token,
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        )


class Kaseya(unittest.TestCase):
    def _transport(self, method, url, headers=None, params=None, json_body=None):
        if url.endswith("/api/v1.0/auth"):
            assert (headers or {}).get("Authorization", "").startswith("Basic ")
            return 200, {"Result": {"Token": "sess-tok"}}
        if url.endswith("/system/orgs"):
            assert (headers or {}).get("Authorization") == "Bearer sess-tok"
            return 200, {"Result": [{"OrgName": "Acme"}]}
        if "/assetmgmt/assets" in url:
            skip = (params or {}).get("$skip", 0)
            top = (params or {}).get("$top", 100)
            data = [{"AgentId": i, "AssetName": f"PC{i}", "OSType": "Windows"} for i in range(3)]
            return 200, {"Result": data[skip:skip + top], "TotalRecords": len(data)}
        return 200, {}

    def test_user_pass_login_then_bearer_and_pagination(self):
        k = KaseyaClient("https://x", "svc-user", "svc-pass", transport=self._transport)
        self.assertEqual(len(k.get_assets()), 3)   # logs in, then pages assets
        self.assertTrue(k.probe()["ok"])            # reuses the cached bearer token

    def test_static_token_skips_login(self):
        k = KaseyaClient("https://x", token="static", transport=self._transport)
        self.assertEqual(len(k.get_assets()), 3)

    def test_no_creds_fails_closed(self):
        with self.assertRaises(RuntimeError):
            KaseyaClient("https://x", transport=self._transport).get_assets()


class Cylance(unittest.TestCase):
    def _transport(self, method, url, headers=None, params=None, json_body=None):
        if url.endswith("/auth/v2/token"):
            self.assertEqual(json_body and "auth_token" in json_body, True)
            return 200, {"access_token": "bearer-abc"}
        if "/devices/v2" in url:
            page = (params or {}).get("page_number", 1)
            return (200, {"page_items": [{"id": "1", "name": "d1", "state": "Online"}]}) if page == 1 \
                else (200, {"page_items": []})
        return 200, {}

    def test_token_exchange_and_paginate(self):
        c = CylanceClient("NA", "tid", "aid", "secret", transport=self._transport)
        devices = list(c.get_paginated("/devices/v2"))
        self.assertEqual(len(devices), 1)
        self.assertTrue(c.probe()["ok"])

    def test_bad_region(self):
        with self.assertRaises(ValueError):
            CylanceClient("MARS", "t", "a", "s")

    def test_paginate_stops_on_total_pages_not_short_page(self):
        # Regression for the bogus 10,000 count: simulate an API that ALWAYS returns a full page
        # (never a short one). Termination must come from total_pages, else it runs to max_pages.
        def transport(method, url, headers=None, params=None, json_body=None):
            if url.endswith("/auth/v2/token"):
                return 200, {"access_token": "t"}
            return 200, {"page_items": [{"id": "a"}, {"id": "b"}],  # always "full" for page_size=2
                         "total_pages": 2, "total_number_of_items": 4}
        c = CylanceClient("NA", "t", "a", "s", transport=transport)
        devices = list(c.get_paginated("/devices/v2", page_size=2))
        self.assertEqual(len(devices), 4)   # 2 pages x 2 — stopped by total_pages, not 2*max_pages

    def test_paginate_sends_page_request_param(self):
        # Regression for the 200-cap bug: Cylance's REQUEST param is `page` (it only ECHOES
        # `page_number`). Sending the wrong name => API returns page 1 forever. Assert `page` advances.
        seen_pages = []

        def transport(method, url, headers=None, params=None, json_body=None):
            if url.endswith("/auth/v2/token"):
                return 200, {"access_token": "t"}
            seen_pages.append((params or {}).get("page"))
            pg = (params or {}).get("page", 1)
            # honor `page`: page 1 full, page 2 short -> stops
            return (200, {"page_items": [{"id": "a"}, {"id": "b"}], "total_pages": 2}) if pg == 1 \
                else (200, {"page_items": [{"id": "c"}], "total_pages": 2})

        c = CylanceClient("NA", "t", "a", "s", transport=transport)
        devices = list(c.get_paginated("/devices/v2", page_size=2))
        self.assertEqual(seen_pages, [1, 2])          # the REQUEST advanced the page
        self.assertEqual([d["id"] for d in devices], ["a", "b", "c"])  # real distinct enumeration


class Huntress(unittest.TestCase):
    def _transport(self, method, url, headers=None, params=None, json_body=None):
        if url.endswith("/account"):
            return 200, {"name": "Acme MSP"}
        if "/agents" in url:
            page = (params or {}).get("page", 1)
            return (200, {"agents": [{"id": 1, "hostname": "h1"}]}) if page == 1 \
                else (200, {"agents": []})
        return 200, {}

    def test_basic_auth_and_paginate(self):
        h = HuntressClient("key", "secret", transport=self._transport)
        self.assertEqual(len(list(h.get_paginated("/agents"))), 1)
        self.assertIn("Acme", h.probe()["detail"])

    def test_requires_both_creds(self):
        with self.assertRaises(ValueError):
            HuntressClient("", "secret")


class Freshdesk(unittest.TestCase):
    from execution.clients.freshdesk import FreshdeskClient as _C

    def test_domain_normalization_and_auth(self):
        from execution.clients.freshdesk import FreshdeskClient
        for given in ("acme", "acme.freshdesk.com", "https://acme.freshdesk.com/"):
            self.assertEqual(FreshdeskClient(given, "k").base, "https://acme.freshdesk.com/api/v2")
        with self.assertRaises(ValueError):
            FreshdeskClient("", "k")

    def test_paginate_list_and_search_wrap(self):
        from execution.clients.freshdesk import FreshdeskClient

        def t(method, url, headers=None, params=None, json_body=None):
            page = (params or {}).get("page", 1)
            if "/search/tickets" in url:                # search wraps in {results,total}
                return (200, {"results": [{"id": 1}], "total": 1}) if page == 1 else (200, {"results": []})
            return (200, [{"id": 1}, {"id": 2}]) if page == 1 else (200, [])   # bare array
        c = FreshdeskClient("acme", "k", transport=t)
        self.assertEqual(len(list(c.get_paginated("/tickets", per_page=2))), 2)
        self.assertEqual(len(list(c.get_paginated("/search/tickets", per_page=30))), 1)

    def test_write_allowlist(self):
        calls = []

        def t(method, url, headers=None, params=None, json_body=None):
            calls.append((method, url))
            return 200, {"id": 7}
        c = FreshdeskClient("acme", "k", transport=t) if False else self._C("acme", "k", transport=t)
        self.assertNotIn("error", c.write("POST", "/tickets", {"subject": "x"}))
        self.assertNotIn("error", c.write("PUT", "/tickets/5", {"status": 4}))
        self.assertNotIn("error", c.write("POST", "/tickets/5/reply", {"body": "hi"}))
        self.assertIn("error", c.write("DELETE", "/tickets/5"))          # destructive path
        self.assertIn("error", c.write("POST", "/agents", {}))           # not writable
        self.assertNotIn("error", c.write_destructive("DELETE", "/tickets/5"))
        self.assertIn("error", c.write_destructive("PUT", "/tickets/5", {}))
        self.assertEqual(len(calls), 4)                                  # 3 writes + 1 destructive


class CylanceWrites(unittest.TestCase):
    """D-82 — bounded write surface: only allow-listed method+path shapes may mutate."""

    def _transport(self, calls):
        def t(method, url, headers=None, params=None, json_body=None):
            if url.endswith("/auth/v2/token"):
                return 200, {"access_token": "t"}
            calls.append((method, url, json_body))
            return 200, {"ok": True}
        return t

    def test_allowed_writes_pass_through(self):
        calls = []
        c = CylanceClient("NA", "t", "a", "s", transport=self._transport(calls))
        self.assertNotIn("error", c.write("PUT", "/devices/v2/dev-1", {"name": "x", "policy_id": "p"}))
        self.assertNotIn("error", c.write("PUT", "/devices/v2/dev-1/threats",
                                          {"threat_id": "a"*64, "event": "Quarantine"}))
        self.assertNotIn("error", c.write("POST", "/globallists/v2", {"sha256": "a"*64}))
        self.assertNotIn("error", c.write("DELETE", "/globallists/v2", {"sha256": "a"*64}))
        self.assertEqual(len(calls), 4)

    def test_disallowed_writes_blocked(self):
        c = CylanceClient("NA", "t", "a", "s", transport=self._transport([]))
        self.assertIn("error", c.write("DELETE", "/devices/v2", {"device_ids": ["x"]}))  # destructive path
        self.assertIn("error", c.write("POST", "/users/v2", {}))      # not writable
        self.assertIn("error", c.write("GET", "/devices/v2/x", {}))   # wrong method

    def test_destructive_split(self):
        calls = []
        c = CylanceClient("NA", "t", "a", "s", transport=self._transport(calls))
        self.assertNotIn("error", c.write_destructive("DELETE", "/devices/v2", {"device_ids": ["d"]}))
        self.assertIn("error", c.write_destructive("PUT", "/devices/v2/x", {}))  # not destructive
        self.assertEqual(len(calls), 1)


class HuntressWrites(unittest.TestCase):
    """D-82 — Huntress's first write APIs, allow-listed."""

    def _transport(self, calls):
        def t(method, url, headers=None, params=None, json_body=None):
            calls.append((method, url, json_body))
            return 200, {"ok": True}
        return t

    def test_allowed_and_blocked(self):
        calls = []
        h = HuntressClient("k", "s", transport=self._transport(calls))
        self.assertNotIn("error", h.write("POST", "/escalations/42/resolution", {}))
        self.assertNotIn("error", h.write("POST", "/incident_reports/9/resolution", {}))
        self.assertNotIn("error", h.write(
            "POST", "/accounts/3/incident_reports/9/remediations/bulk_approval", {}))
        self.assertEqual(len(calls), 3)
        # blocked: unknown path + wrong method
        self.assertIn("error", h.write("POST", "/agents/1/isolate", {}))
        self.assertIn("error", h.write("DELETE", "/escalations/42/resolution", {}))


if __name__ == "__main__":
    unittest.main()
