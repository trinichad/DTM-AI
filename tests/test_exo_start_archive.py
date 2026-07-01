"""exo_start_archive (D-113) — force-start the Managed Folder Assistant.

Proves: it resolves the Primary mailbox GUID (Get-Mailbox.ExchangeGuid) and targets
Start-ManagedFolderAssistant at THAT guid (not the email); the batch path returns a per-row
results list with attribution + a `started` count; a missing mailbox is a failed row, not a raise;
and a mailbox with no ExchangeGuid fails cleanly at the locate step.
"""
import unittest

from execution.core.context import ToolContext


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


class FakeEXO:
    def __init__(self, boxes):
        self.boxes = {k.lower(): v for k, v in boxes.items()}
        self.calls = []

    def invoke(self, cmdlet, params=None):
        params = params or {}
        self.calls.append((cmdlet, dict(params)))
        if cmdlet == "Get-Mailbox":
            ident = str(params.get("Identity", "")).lower()
            mb = self.boxes.get(ident)
            return [mb] if mb else {"error": "couldn't be found"}
        if cmdlet == "Start-ManagedFolderAssistant":
            return {"ok": True}          # cmdlet emits no output → connector returns {"ok": True}
        return {"error": f"unexpected cmdlet {cmdlet}"}


def _boxes():
    return {"a@x.com": {"PrimarySmtpAddress": "a@x.com", "ExchangeGuid": "aaaaaaaa-0000-0000-0000-000000000001"},
            "b@x.com": {"PrimarySmtpAddress": "b@x.com", "ExchangeGuid": "bbbbbbbb-0000-0000-0000-000000000002"}}


class StartArchive(unittest.TestCase):
    def test_single_targets_primary_guid(self):
        from execution.skills import exo_start_archive
        fake = FakeEXO(_boxes())
        r = exo_start_archive.run(_ctx(fake), identity="a@x.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["managed_folder_assistant"], "started")
        self.assertEqual(r["primary_guid"], "aaaaaaaa-0000-0000-0000-000000000001")
        # MFA was targeted at the GUID, not the email
        start = [c for c in fake.calls if c[0] == "Start-ManagedFolderAssistant"]
        self.assertEqual(start[0][1]["Identity"], "aaaaaaaa-0000-0000-0000-000000000001")
        self.assertNotIn("results", r)

    def test_list_starts_each_and_attributes_rows(self):
        from execution.skills import exo_start_archive
        fake = FakeEXO(_boxes())
        r = exo_start_archive.run(_ctx(fake), identities=["a@x.com", "b@x.com"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["started"], 2)
        self.assertEqual(r["ok_count"], 2)
        by = {row["identity"]: row for row in r["results"]}
        self.assertEqual(by["a@x.com"]["primary_guid"], "aaaaaaaa-0000-0000-0000-000000000001")
        self.assertEqual(by["b@x.com"]["primary_guid"], "bbbbbbbb-0000-0000-0000-000000000002")

    def test_missing_mailbox_in_list_is_a_failed_row_not_a_raise(self):
        from execution.skills import exo_start_archive
        fake = FakeEXO(_boxes())
        r = exo_start_archive.run(_ctx(fake), identities=["a@x.com", "ghost@x.com"])
        self.assertTrue(r["ok"])                          # one succeeded
        self.assertEqual(r["ok_count"], 1)
        by = {row["identity"]: row for row in r["results"]}
        self.assertFalse(by["ghost@x.com"]["ok"])

    def test_no_exchange_guid_fails_at_locate(self):
        from execution.skills import exo_start_archive
        fake = FakeEXO({"c@x.com": {"PrimarySmtpAddress": "c@x.com"}})  # no ExchangeGuid
        r = exo_start_archive.run(_ctx(fake), identity="c@x.com")
        self.assertFalse(r["ok"])
        self.assertEqual(r["step"], "locate")
        # never reached the start cmdlet
        self.assertFalse([c for c in fake.calls if c[0] == "Start-ManagedFolderAssistant"])


if __name__ == "__main__":
    unittest.main()
