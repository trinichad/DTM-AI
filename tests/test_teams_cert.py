"""Teams app-certificate tests (D-29 amendment) — generation, assertion, token-flow switch."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from execution.core import teams_cert


def _cfg(path):
    return SimpleNamespace(get=lambda k, d=None: str(path) if k == "MSPAI_TEAMS_CERT_PATH" else d)


class CertLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "cert.pem"
        self.cfg = _cfg(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_generate_info_delete(self):
        self.assertFalse(teams_cert.exists(self.cfg))
        r = teams_cert.generate(self.cfg)
        self.assertTrue(r["exists"])
        self.assertEqual(len(r["thumbprint"]), 40)               # sha1 hex
        self.assertIn("BEGIN CERTIFICATE", r["public_pem"])
        self.assertNotIn("PRIVATE KEY", r["public_pem"])          # public half only
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertIn("PRIVATE KEY", self.path.read_text())       # key stays on disk only
        self.assertTrue(teams_cert.delete(self.cfg))
        self.assertFalse(teams_cert.exists(self.cfg))

    def test_client_assertion_is_valid_rs256(self):
        teams_cert.generate(self.cfg)
        token = teams_cert.client_assertion("cid-1", "tid-1", self.cfg)
        import jwt as pyjwt
        header = pyjwt.get_unverified_header(token)
        self.assertEqual(header["alg"], "RS256")
        self.assertTrue(header["x5t"])
        claims = pyjwt.decode(token, options={"verify_signature": False},
                              audience="https://login.microsoftonline.com/tid-1/oauth2/v2.0/token")
        self.assertEqual(claims["iss"], "cid-1")
        self.assertEqual(claims["sub"], "cid-1")
        self.assertLess(claims["nbf"], claims["exp"])
        # verifies against the cert's own public key
        from cryptography.hazmat.primitives import serialization
        from cryptography.x509 import load_pem_x509_certificate
        cert = load_pem_x509_certificate(self.path.read_bytes())
        pyjwt.decode(token, cert.public_key(), algorithms=["RS256"],
                     audience="https://login.microsoftonline.com/tid-1/oauth2/v2.0/token")

    def test_assertion_without_cert_fails_closed(self):
        with self.assertRaises(ValueError):
            teams_cert.client_assertion("cid", "tid", self.cfg)


class TokenFlowSwitch(unittest.TestCase):
    """TeamsClient: secret mode by default; certificate mode the moment a cert exists."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "cert.pem"
        os.environ["MSPAI_TEAMS_CERT_PATH"] = str(self.path)

    def tearDown(self):
        os.environ.pop("MSPAI_TEAMS_CERT_PATH", None)
        self.tmp.cleanup()

    def test_secret_mode(self):
        from execution.clients.msteams import TeamsClient
        c = TeamsClient("cid", "sek", "tid")
        f = c._token_request_fields()
        self.assertEqual(f["client_secret"], "sek")
        self.assertNotIn("client_assertion", f)
        self.assertEqual(c.auth_method, "client_secret")

    def test_cert_mode_wins_when_cert_exists(self):
        teams_cert.generate()                       # global config → env path override
        from execution.clients.msteams import TeamsClient
        c = TeamsClient("cid", "sek", "tid")
        f = c._token_request_fields()
        self.assertNotIn("client_secret", f)
        self.assertEqual(f["client_assertion_type"],
                         "urn:ietf:params:oauth:client-assertion-type:jwt-bearer")
        self.assertTrue(f["client_assertion"])
        self.assertEqual(c.auth_method, "certificate")

    def test_no_secret_no_cert_fails_closed(self):
        from execution.clients.msteams import TeamsClient
        with self.assertRaises(ValueError):
            TeamsClient("cid", "", "tid")

    def test_cert_only_is_enough(self):
        teams_cert.generate()
        from execution.clients.msteams import TeamsClient
        c = TeamsClient("cid", "", "tid")           # no secret — cert satisfies auth
        self.assertEqual(c.auth_method, "certificate")


if __name__ == "__main__":
    unittest.main()
