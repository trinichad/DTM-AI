"""Secret store + credential-set tests — prove UI credential entry is safe."""
import os
import stat
import tempfile
import unittest
from pathlib import Path

from execution.core.config import Config
from execution.core.secrets_store import SecretStore, SecretStoreError
from execution.core import credentials


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "secrets.local"
        self.s = SecretStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_get_and_0600(self):
        self.s.set_many({"HUNTRESS_API_KEY": "abc"}, allowed_keys={"HUNTRESS_API_KEY"})
        self.assertEqual(self.s.get("HUNTRESS_API_KEY"), "abc")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_empty_clears(self):
        self.s.set_many({"K": "v"}, allowed_keys={"K"})
        self.s.set_many({"K": ""}, allowed_keys={"K"})
        self.assertIsNone(self.s.get("K"))

    def test_allowlist_enforced(self):
        with self.assertRaises(SecretStoreError):
            self.s.set_many({"PATH": "/evil"}, allowed_keys={"HUNTRESS_API_KEY"})

    def test_refuses_world_readable(self):
        if os.name != "posix":
            self.skipTest("posix-only")
        self.path.write_text("X=1\n")
        self.path.chmod(0o644)
        with self.assertRaises(SecretStoreError):
            SecretStore(self.path)


class CredentialSet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = Path(self.tmp.name) / ".env"
        env.write_text("DTM_ENV=dev\n")
        env.chmod(0o600)
        self.cfg = Config(env_path=env, secret_store=SecretStore(Path(self.tmp.name) / "secrets.local"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_integration_roundtrip_fingerprint_only(self):
        st = credentials.set_integration(
            "huntress", {"HUNTRESS_API_KEY": "key123", "HUNTRESS_API_SECRET": "sec456"}, self.cfg)
        self.assertTrue(st.configured)
        # status exposes fingerprints, never the raw secret
        self.assertIn("HUNTRESS_API_KEY", st.fingerprints)
        self.assertNotIn("key123", str(st.fingerprints))
        # and the value is actually usable by require()
        self.assertEqual(credentials.require("huntress", self.cfg)["HUNTRESS_API_KEY"], "key123")

    def test_rejects_foreign_key(self):
        with self.assertRaises(credentials.MissingCredential):
            credentials.set_integration("huntress", {"KASEYA_TOKEN": "x"}, self.cfg)

    def test_rejects_unknown_integration(self):
        with self.assertRaises(credentials.MissingCredential):
            credentials.set_integration("acme", {"X": "y"}, self.cfg)

    def test_partial_then_complete(self):
        credentials.set_integration("cylance", {"CYLANCE_TENANT_ID": "t"}, self.cfg)
        self.assertFalse(credentials.is_configured("cylance", self.cfg))
        credentials.set_integration("cylance", {"CYLANCE_APP_ID": "a", "CYLANCE_APP_SECRET": "s"}, self.cfg)
        self.assertTrue(credentials.is_configured("cylance", self.cfg))


if __name__ == "__main__":
    unittest.main()
