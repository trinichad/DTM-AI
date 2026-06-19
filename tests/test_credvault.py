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
        self.cfg = StubCfg({"MSPAI_VAULT_PATH": self.tmp.name})
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
        self.cfg.d["MSPAI_CREDVAULT_TTL_MIN"] = 0          # already expired window
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


class MultiAdminSlots(unittest.TestCase):
    """D-30: per-admin passphrase slots + lost-passphrase recovery + agent auto-unlock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = StubCfg({"MSPAI_VAULT_PATH": self.tmp.name})
        self.v = CredVault(self.cfg)
        self.v.set_passphrase("alex's passphrase", username="alex")
        self.v.upsert("acme", "o365", {"username": "a@b.com", "password": "Secret123"})

    def tearDown(self):
        self.tmp.cleanup()

    def _new(self):
        return CredVault(self.cfg)

    def test_second_admin_slot_unlocks_too(self):
        self.v.set_slot("dana", "dana's passphrase", by="alex")
        v2 = self._new()
        self.assertTrue(v2.unlock("dana's passphrase", username="dana"))
        self.assertEqual(v2.admin_list("acme")[0]["label"], "o365")
        v3 = self._new()
        self.assertTrue(v3.unlock("alex's passphrase", username="alex"))

    def test_lost_passphrase_recovery_by_other_admin(self):
        self.v.set_slot("dana", "dana's passphrase", by="alex")
        # alex loses his passphrase; dana unlocks and resets alex's slot
        v2 = self._new()
        self.assertTrue(v2.unlock("dana's passphrase", username="dana"))
        v2.set_slot("alex", "alex's NEW passphrase", by="dana")
        v3 = self._new()
        self.assertFalse(v3.unlock("alex's passphrase", username="alex"))     # old gone
        self.assertTrue(v3.unlock("alex's NEW passphrase", username="alex"))
        self.assertEqual(v3.resolve("acme", "o365")["fields"]["password"], "Secret123")

    def test_set_slot_requires_unlocked(self):
        v2 = self._new()
        with self.assertRaises(VaultLocked):
            v2.set_slot("dana", "whatever-passphrase", by="dana")

    def test_cannot_remove_last_slot(self):
        with self.assertRaises(ValueError):
            self.v.delete_slot("alex")
        self.v.set_slot("dana", "dana's passphrase", by="alex")
        self.v.delete_slot("dana")
        self.assertEqual([s["username"] for s in self.v.slots()], ["alex"])

    def test_change_passphrase_does_not_touch_other_slots(self):
        self.v.set_slot("dana", "dana's passphrase", by="alex")
        self.v.change_passphrase("alex's passphrase", "alex's new one!", username="alex")
        v2 = self._new()
        self.assertTrue(v2.unlock("dana's passphrase", username="dana"))      # dana unaffected
        v3 = self._new()
        self.assertTrue(v3.unlock("alex's new one!", username="alex"))

    def test_v1_vault_migrates_on_unlock(self):
        import base64, hashlib, json as _json
        from cryptography.fernet import Fernet
        from pathlib import Path
        import secrets as _secrets
        root = Path(self.tmp.name + "_v1")
        cfg = StubCfg({"MSPAI_VAULT_PATH": str(root)})
        # craft an old-format meta exactly like the v1 writer did
        salt = _secrets.token_bytes(16)
        raw = hashlib.scrypt(b"old master pass", salt=salt, n=2**14, r=8, p=1, dklen=32)
        key = base64.urlsafe_b64encode(raw)
        meta_dir = root / "clients"; meta_dir.mkdir(parents=True)
        (meta_dir / ".credvault.json").write_text(_json.dumps({
            "salt": base64.b64encode(salt).decode(), "kdf": "scrypt",
            "verifier": Fernet(key).encrypt(b"mspai-credvault-ok").decode()}))
        v1 = CredVault(cfg)
        v1._key = key; v1._expires = 9e18                      # simulate the old unlocked state
        v1.upsert("acme", "fw", {"password": "OldData1"})
        v1.lock()
        # fresh handle, old passphrase → migrates to slots, data intact
        v2 = CredVault(cfg)
        self.assertFalse(v2.unlock("wrong", username="alex"))
        self.assertTrue(v2.unlock("old master pass", username="alex"))
        self.assertEqual([s["username"] for s in v2.slots()], ["alex"])
        self.assertEqual(v2.resolve("acme", "fw")["fields"]["password"], "OldData1")

    # ── agent auto-unlock (the Teams-while-away scenario) ──
    def test_auto_unlock_survives_restart(self):
        self.v.set_service_unlock(True)
        v2 = self._new()                                       # "restart": no key in memory
        self.assertTrue(v2.status()["auto_unlock"])
        self.assertTrue(v2.status()["unlocked"])
        # no passphrase ever entered — resolve works (the agent's unattended path)
        self.assertEqual(v2.resolve("acme", "o365")["fields"]["password"], "Secret123")

    def test_auto_unlock_disable_locks_again(self):
        self.v.set_service_unlock(True)
        self.v.set_service_unlock(False)
        v2 = self._new()
        self.assertFalse(v2.status()["auto_unlock"])
        with self.assertRaises(VaultLocked):
            v2.resolve("acme", "o365")

    def test_auto_unlock_enable_requires_unlocked(self):
        v2 = self._new()
        with self.assertRaises(VaultLocked):
            v2.set_service_unlock(True)

    def test_keyfile_alone_is_not_the_dek(self):
        from pathlib import Path
        self.v.set_service_unlock(True)
        keyfile = Path(self.tmp.name) / "clients" / ".credvault.service.key"
        self.assertTrue(keyfile.is_file())
        self.assertEqual(keyfile.stat().st_mode & 0o777, 0o600)
        # the key file is a wrapping key, not the data key itself
        self.assertNotEqual(keyfile.read_bytes().strip(), self.v._live_key())
