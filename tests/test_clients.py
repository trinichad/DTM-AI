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
        if url.endswith("/system/orgs"):
            return 200, {"Result": [{"OrgName": "Acme"}]}
        if "/assetmgmt/assets" in url:
            skip = (params or {}).get("$skip", 0)
            top = (params or {}).get("$top", 100)
            data = [{"AgentId": i, "AssetName": f"PC{i}", "OSType": "Windows"} for i in range(3)]
            return 200, {"Result": data[skip:skip + top], "TotalRecords": len(data)}
        return 200, {}

    def test_static_token_no_login_and_pagination(self):
        k = KaseyaClient("https://x", token="static", transport=self._transport)
        self.assertEqual(len(k.get_assets()), 3)
        self.assertTrue(k.probe()["ok"])


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


if __name__ == "__main__":
    unittest.main()
