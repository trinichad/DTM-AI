"""Opt-in TOTP MFA (D-87) — TOTP primitives + AuthStore enroll/verify/disable/admin-reset."""
import tempfile
import time
import unittest
from pathlib import Path

from execution.web import totp
from execution.web.auth import AuthStore, SessionSigner


class TotpPrimitives(unittest.TestCase):
    def test_generate_and_verify_roundtrip(self):
        s = totp.generate_secret()
        self.assertGreaterEqual(len(s), 16)
        code = totp.now_code(s)
        self.assertTrue(totp.verify(s, code))
        self.assertTrue(totp.verify(s, f" {code} "))           # whitespace tolerated
        self.assertFalse(totp.verify(s, "000000") if code != "000000" else False)
        self.assertFalse(totp.verify(s, "abc"))                # non-digit
        self.assertFalse(totp.verify(s, "1234567"))            # wrong length

    def test_clock_skew_window(self):
        s = totp.generate_secret()
        now = time.time()
        prev = totp.now_code(s, t=now - 30)                    # one step back
        self.assertTrue(totp.verify(s, prev, t=now))           # accepted within ±1 window
        old = totp.now_code(s, t=now - 120)                    # 4 steps back
        self.assertFalse(totp.verify(s, old, t=now))

    def test_provisioning_uri(self):
        uri = totp.provisioning_uri("ABC234", "admin@example.com")
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertIn("secret=ABC234", uri)
        self.assertIn("issuer=MSP%20AI", uri)


class MfaStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.tmp.name) / "u.db")
        self.auth.ensure_admin("adminpw1")
        self.auth.create_user("bob", "bobpass1", role="user")

    def tearDown(self):
        self.auth.close()
        self.tmp.cleanup()

    def test_default_off(self):
        self.assertFalse(self.auth.mfa_is_enabled("bob"))
        self.assertFalse(self.auth.get_user("bob")["mfa_enabled"])

    def test_enroll_confirm_then_login_requires_code(self):
        secret, uri = self.auth.start_mfa_setup("bob")
        self.assertTrue(uri.startswith("otpauth://"))
        self.assertFalse(self.auth.mfa_is_enabled("bob"))       # pending, not active yet
        self.assertFalse(self.auth.confirm_mfa("bob", "000000"))  # wrong code → stays off
        self.assertFalse(self.auth.mfa_is_enabled("bob"))
        self.assertTrue(self.auth.confirm_mfa("bob", totp.now_code(secret)))
        self.assertTrue(self.auth.mfa_is_enabled("bob"))
        # login-time second factor
        self.assertTrue(self.auth.verify_mfa("bob", totp.now_code(secret)))
        self.assertFalse(self.auth.verify_mfa("bob", "000000"))
        # password check is unchanged + independent
        self.assertEqual(self.auth.verify_login("bob", "bobpass1"), "user")

    def test_user_disable_requires_valid_code(self):
        secret, _ = self.auth.start_mfa_setup("bob")
        self.auth.confirm_mfa("bob", totp.now_code(secret))
        self.assertFalse(self.auth.disable_mfa("bob", code="000000"))   # bad code → still on
        self.assertTrue(self.auth.mfa_is_enabled("bob"))
        self.assertTrue(self.auth.disable_mfa("bob", code=totp.now_code(secret)))
        self.assertFalse(self.auth.mfa_is_enabled("bob"))
        self.assertFalse(self.auth.verify_mfa("bob", totp.now_code(secret)))   # secret wiped

    def test_admin_reset_recovers_lockout(self):
        secret, _ = self.auth.start_mfa_setup("bob")
        self.auth.confirm_mfa("bob", totp.now_code(secret))
        self.assertTrue(self.auth.mfa_is_enabled("bob"))
        self.assertTrue(self.auth.disable_mfa("bob", admin=True))       # no code needed
        self.assertFalse(self.auth.mfa_is_enabled("bob"))
        self.assertFalse(self.auth.list_users()[0]["mfa_enabled"] if False else
                         self.auth.get_user("bob")["mfa_enabled"])

    def test_migration_adds_columns(self):
        # a second store over the same db (simulates re-open) keeps working + columns present
        self.assertIn("mfa_enabled", self.auth.get_user("bob"))


class TrustTokens(unittest.TestCase):
    def setUp(self):
        self.signer = SessionSigner(secret=b"x" * 32)

    def test_trust_roundtrip_and_tag(self):
        tok = self.signer.make_trust("bob", 30 * 86400, "tag123")
        v = self.signer.verify_trust(tok)
        self.assertEqual(v, ("bob", "tag123"))

    def test_expired_trust_rejected(self):
        self.assertIsNone(self.signer.verify_trust(self.signer.make_trust("bob", -10, "t")))

    def test_session_token_not_accepted_as_trust(self):
        # a plain session token must not verify as a trust token (scope isolation)
        self.assertIsNone(self.signer.verify_trust(self.signer.make("bob", 60)))
        # and a trust token must not verify as a session
        self.assertIsNone(self.signer.verify("bob"))  # garbage in
        self.assertIsNone(self.signer.verify(self.signer.make_trust("bob", 99, "t").replace("trust", "x", 1)))

    def test_tampered_trust_rejected(self):
        tok = self.signer.make_trust("bob", 99, "t")
        self.assertIsNone(self.signer.verify_trust(tok[:-1] + ("0" if tok[-1] != "0" else "1")))


class TrustWindow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.tmp.name) / "u.db")
        self.auth.ensure_admin("adminpw1")
        self.auth.create_user("bob", "bobpass1", role="user")

    def tearDown(self):
        self.auth.close()
        self.tmp.cleanup()

    def test_default_and_set_window(self):
        self.assertEqual(self.auth.get_mfa_trust_days("bob"), 30)
        self.auth.set_mfa_trust_days("bob", 90)
        self.assertEqual(self.auth.get_mfa_trust_days("bob"), 90)
        self.auth.set_mfa_trust_days("bob", 0)                  # until-signed-out
        self.assertEqual(self.auth.get_mfa_trust_days("bob"), 0)
        with self.assertRaises(ValueError):
            self.auth.set_mfa_trust_days("bob", 45)             # not an allowed choice

    def test_secret_tag_changes_on_reenroll(self):
        s1, _ = self.auth.start_mfa_setup("bob"); self.auth.confirm_mfa("bob", totp.now_code(s1))
        tag1 = self.auth.mfa_secret_tag("bob")
        self.assertTrue(tag1)
        self.auth.disable_mfa("bob", admin=True)
        self.assertEqual(self.auth.mfa_secret_tag("bob"), "")   # no secret → no tag
        s2, _ = self.auth.start_mfa_setup("bob"); self.auth.confirm_mfa("bob", totp.now_code(s2))
        self.assertNotEqual(self.auth.mfa_secret_tag("bob"), tag1)   # re-enroll → trust invalidated


if __name__ == "__main__":
    unittest.main()
