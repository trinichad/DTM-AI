"""Memory + KB tests — vault search/read/write, path-safety, and the skills via dispatch."""
import tempfile
import unittest
from pathlib import Path

from execution.core.audit import AuditStore
from execution.core.capabilities import CapabilityStore
from execution.core.context import ToolContext
from execution.core.dispatch import dispatch
from execution.core.gates import ConfigurableApprovalGate
from execution.core.memory import VaultStore, _safe_tenant
from execution.core.registry import Registry


class Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.v = VaultStore(path=Path(self.tmp.name))
        # isolate from the repo-bundled reference/ so these per-vault assertions are deterministic
        self.v.reference_dir = Path(self.tmp.name) / "_noref"
        (self.v.kb_dir / "net").mkdir(parents=True)
        (self.v.kb_dir / "net" / "firewall.md").write_text(
            "# SonicWall\nTo reset the admin password, hold reset 15s then use mgmt port.", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_kb_search_finds_and_ranks(self):
        hits = self.v.search_kb("reset admin password")
        self.assertTrue(hits)
        self.assertIn("firewall.md", hits[0]["doc"])
        self.assertIn("reset", hits[0]["snippet"].lower())

    def test_kb_search_requires_all_terms(self):
        self.assertEqual(self.v.search_kb("reset zebrafish"), [])

    def test_kb_search_includes_bundled_reference(self):
        # the repo-bundled reference/ ships with the app and is searchable alongside the vault kb/
        v = VaultStore(path=Path(self.tmp.name))   # default reference_dir = the real repo reference/
        hits = v.search_kb("kaseya executePowershell command")
        self.assertTrue(any("reference/" in h["doc"] for h in hits),
                        "bundled Kaseya command reference should be searchable via kb_search")

    def test_memory_roundtrip(self):
        self.assertEqual(self.v.read_memory("acme"), "")
        res = self.v.append_memory("acme", "prefers maintenance windows on Sundays", "tech1")
        self.assertTrue(res["ok"])
        text = self.v.read_memory("acme")
        self.assertIn("Sundays", text)
        self.assertIn("tech1", text)

    def test_memory_rejects_wildcard_tenant(self):
        self.assertIn("error", self.v.append_memory("*", "x", "t"))

    def test_path_traversal_sanitized(self):
        self.assertNotIn("/", _safe_tenant("../../etc/passwd"))
        self.assertNotIn("..", _safe_tenant("..%2f.."))

    def test_read_kb_doc(self):
        doc = next(d for d in self.v.list_kb() if d.endswith("firewall.md"))
        content = self.v.read_kb_doc(doc)
        self.assertIn("SonicWall", content)
        self.assertIn("reset", content.lower())

    def test_read_kb_doc_unknown_or_traversal_is_none(self):
        self.assertIsNone(self.v.read_kb_doc("kb/does/not/exist.md"))
        self.assertIsNone(self.v.read_kb_doc("../../etc/passwd"))  # not enumerated → None, no traversal

    def test_client_registry(self):
        self.assertEqual(self.v.list_clients(), [])
        self.assertTrue(self.v.add_client("acme")["ok"])
        self.assertIn("acme", self.v.list_clients())               # registered with no memory yet
        self.assertIn("error", self.v.add_client("*"))             # wildcard rejected
        self.assertTrue(self.v.remove_client("acme")["ok"])
        self.assertNotIn("acme", self.v.list_clients())
        self.assertIn("error", self.v.remove_client("ghost"))

    def test_kb_write_and_delete(self):
        r = self.v.write_kb_doc("runbooks/onboarding", "# Onboarding\nstep 1")
        self.assertTrue(r["ok"])
        self.assertEqual(r["doc"], "kb/runbooks/onboarding.md")    # .md appended, lands under kb/
        self.assertIn("Onboarding", self.v.read_kb_doc("kb/runbooks/onboarding.md"))
        self.assertIn("kb/runbooks/onboarding.md", self.v.list_kb())
        self.assertTrue(self.v.delete_kb_doc("kb/runbooks/onboarding.md")["ok"])
        self.assertIsNone(self.v.read_kb_doc("kb/runbooks/onboarding.md"))

    def test_kb_write_traversal_and_reference_protected(self):
        self.assertIn("error", self.v.write_kb_doc("../../etc/evil", "x"))      # traversal blocked
        self.assertIn("error", self.v.delete_kb_doc("reference/anything.md"))    # bundled = read-only

    def test_kb_rename(self):
        self.v.write_kb_doc("runbooks/old", "# Old\nbody")
        r = self.v.rename_kb_doc("kb/runbooks/old.md", "runbooks/new")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["to"], "kb/runbooks/new.md")                          # .md appended, under kb/
        self.assertIsNone(self.v.read_kb_doc("kb/runbooks/old.md"))             # old path gone
        self.assertIn("Old", self.v.read_kb_doc("kb/runbooks/new.md"))         # content preserved
        # protections: bundled read-only, traversal, and no clobber
        self.assertIn("error", self.v.rename_kb_doc("reference/x.md", "y"))
        self.assertIn("error", self.v.rename_kb_doc("kb/runbooks/new.md", "../../etc/evil"))
        self.v.write_kb_doc("runbooks/taken", "x")
        self.assertIn("error", self.v.rename_kb_doc("kb/runbooks/new.md", "runbooks/taken"))  # no clobber


class MemorySkills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "m.db"
        self.audit = AuditStore(db)
        self.caps = CapabilityStore(db)
        self.reg = Registry()
        # seed internal-write policy as runtime.build_agent does
        for t in self.reg.all():
            if t.source == "dtm_ai" and t.is_write:
                self.caps.set(t.name, allow_write=True, require_approval=t.requires_approval)
        self.gate = ConfigurableApprovalGate(self.caps, self.reg)
        import os
        os.environ["DTM_VAULT_PATH"] = str(Path(self.tmp.name) / "vault")
        self.ctx = ToolContext(tenant_id="acme", actor="tech1")

    def tearDown(self):
        import os
        os.environ.pop("DTM_VAULT_PATH", None)
        self.audit.close()
        self.caps.close()
        self.tmp.cleanup()

    def _d(self, name, args=None):
        return dispatch(registry=self.reg, audit=self.audit, ctx=self.ctx, name=name,
                        args=args, gate=self.gate)

    def test_memory_note_then_read(self):
        w = self._d("memory_note", {"note": "VPN renewal due August"})
        self.assertTrue(w["ok"], w)
        r = self._d("memory_read")
        self.assertTrue(r["ok"])
        self.assertIn("VPN renewal", r["data"]["memory"])

    def test_memory_note_blocked_for_wildcard_tenant(self):
        ctx = ToolContext(tenant_id="*", actor="t")
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx, name="memory_note",
                       args={"note": "x"}, gate=self.gate)
        self.assertFalse(env["ok"])  # vault refuses '*' -> {"error":...} -> error envelope


if __name__ == "__main__":
    unittest.main()
