"""CredVault tests — encryption at rest, lock/unlock, the agent-safe view (no values), and the
human-append split-secret guarantee. Security-critical; assertions are deliberately strict."""
import tempfile
import time
import unittest
from pathlib import Path

from execution.core.credvault import AppendRequired, CredVault, VaultLocked


class StubCfg:
    def __init__(self, d): self.d = d
    def get(self, k, default=None): return self.d.get(k, default)
    def int(self, k, default=0):
        try: return int(self.d.get(k, default))
        except (TypeError, ValueError): return default


class CredVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = StubCfg({"DTM_VAULT_PATH": self.tmp.name})
        self.v = CredVault(self.cfg)
        self.v.set_passphrase("correct horse battery")

    def tearDown(self):
        self.tmp.cleanup()

    def _new(self):
        return CredVault(self.cfg)            # a fresh handle = a fresh (locked) process view

    # ── encryption at rest ──
    def test_file_is_encrypted_on_disk(self):
        self.v.upsert("acme", "o365", {"username": "a@b.com", "password": "Secret123"})
        blob = (Path(self.tmp.name) / "clients" / "acme" / "credentials.enc").read_bytes()
        self.assertNotIn(b"Secret123", blob)         # value must not appear in plaintext
        self.assertNotIn(b"a@b.com", blob)

    def test_locked_after_restart_then_unlock(self):
        self.v.upsert("acme", "o365", {"username": "a@b.com", "password": "Secret123"})
        v2 = self._new()
        self.assertFalse(v2.status()["unlocked"])
        with self.assertRaises(VaultLocked):
            v2.admin_list("acme")
        self.assertFalse(v2.unlock("wrong passphrase"))      # wrong → stays locked
        self.assertTrue(v2.unlock("correct horse battery"))
        self.assertEqual(v2.admin_list("acme")[0]["label"], "o365")

    def test_session_expiry(self):
        self.v.upsert("acme", "o365", {"password": "Secret123"})    # a file to decrypt
        self.cfg.d["DTM_CREDVAULT_TTL_MIN"] = 0          # already expired window
        v = CredVault(self.cfg)
        self.assertTrue(v.unlock("correct horse battery"))
        time.sleep(0.01)
        with self.assertRaises(VaultLocked):
            v.admin_list("acme")

    def test_change_passphrase_reencrypts(self):
        self.v.upsert("acme", "o365", {"username": "a@b.com", "password": "Secret123"})
        self.v.change_passphrase("correct horse battery", "a different passphrase!")
        v2 = self._new()
        self.assertFalse(v2.unlock("correct horse battery"))   # old no longer works
        self.assertTrue(v2.unlock("a different passphrase!"))
        self.assertEqual(v2.admin_list("acme")[0]["label"], "o365")

    # ── multiple labeled creds + management view (fingerprints only) ──
    def test_multiple_creds_and_no_raw_values_in_admin_view(self):
        self.v.upsert("acme", "o365_global_admin", {"username": "admin@acme.com", "password": "p1"})
        self.v.upsert("acme", "sonicwall_admin", {"username": "fwadmin", "password": "p2", "url": "https://fw"})
        rows = {c["label"]: c for c in self.v.admin_list("acme")}
        self.assertEqual(set(rows), {"o365_global_admin", "sonicwall_admin"})
        # values are fingerprinted, never raw
        for c in rows.values():
            for fp in c["fingerprints"].values():
                self.assertNotIn("p1", fp); self.assertNotIn("p2", fp)
                self.assertTrue(fp == "—" or len(fp) == 7)
        self.v.delete("acme", "sonicwall_admin")
        self.assertEqual([c["label"] for c in self.v.admin_list("acme")], ["o365_global_admin"])

    # ── agent-SAFE view: labels + field names + append flags, NEVER values ──
    def test_safe_list_hides_values(self):
        self.v.upsert("acme", "o365", {"username": "admin@acme.com", "password": "Password123{end_append}"})
        safe = self.v.safe_list("acme")[0]
        self.assertEqual(safe["label"], "o365")
        self.assertEqual(safe["fields"], ["password", "username"])      # names only
        self.assertTrue(safe["needs_append"]["end"])
        self.assertFalse(safe["needs_append"]["start"])
        blob = repr(safe)
        self.assertNotIn("admin@acme.com", blob)
        self.assertNotIn("Password123", blob)

    # ── the human append (split secret) ──
    def test_append_required_and_assembled_server_side(self):
        self.v.upsert("acme", "o365", {"username": "admin@acme.com",
                                       "password": "Password123{end_append}"})
        with self.assertRaises(AppendRequired) as ctx:
            self.v.resolve("acme", "o365")                  # no append supplied → refused
        self.assertTrue(ctx.exception.need["end"])
        full = self.v.resolve("acme", "o365", end="!XyZ")["fields"]["password"]
        self.assertEqual(full, "Password123!XyZ")           # assembled in memory
        # the append literal is NEVER written to disk (even encrypted) — only the placeholder is stored
        v2 = self._new()
        self.assertTrue(v2.unlock("correct horse battery"))
        self.assertEqual(v2.resolve("acme", "o365", end="!XyZ")["fields"]["password"], "Password123!XyZ")
        # decrypt the stored doc and confirm it still holds the placeholder, not the append
        stored_pw = v2._read("acme")["creds"][0]["fields"]["password"]
        self.assertEqual(stored_pw, "Password123{end_append}")
        self.assertNotIn("!XyZ", stored_pw)

    def test_both_appends(self):
        self.v.upsert("acme", "vpn", {"password": "{start_append}-core-{end_append}"})
        need = self.v.safe_list("acme")[0]["needs_append"]
        self.assertTrue(need["start"] and need["end"])
        with self.assertRaises(AppendRequired):
            self.v.resolve("acme", "vpn", end="z")           # missing the start piece
        self.assertEqual(self.v.resolve("acme", "vpn", start="A", end="Z")["fields"]["password"],
                         "A-core-Z")

    def test_test_assemble_reports_fingerprint_not_value(self):
        self.v.upsert("acme", "o365", {"password": "Password123{end_append}"})
        r = self.v.test_assemble("acme", "o365", end="!end")
        self.assertEqual(r["password_len"], len("Password123!end"))
        self.assertEqual(len(r["password_fp"]), 7)
        self.assertNotIn("Password", repr(r))

    def test_edit_merges_fields_no_wipe(self):
        self.v.upsert("acme", "o365", {"username": "a@b.com", "password": "Secret123"})
        self.v.upsert("acme", "o365", {"username": "new@b.com"})   # change only username
        full = self.v.resolve("acme", "o365")["fields"]
        self.assertEqual(full["username"], "new@b.com")
        self.assertEqual(full["password"], "Secret123")            # password preserved, not wiped

    def test_label_validation(self):
        with self.assertRaises(ValueError):
            self.v.upsert("acme", "Bad Label!", {"password": "x"})
        with self.assertRaises(ValueError):
            self.v.upsert("acme", "empty", {"password": ""})   # no non-empty field


if __name__ == "__main__":
    unittest.main()
