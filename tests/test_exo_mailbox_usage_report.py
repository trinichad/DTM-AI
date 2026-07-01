"""exo_mailbox_usage_report (D-114) — tenant-wide size/archive/retention triage.

Proves: it lists every mailbox, computes percent-of-quota from the byte counts, reports archive
state + retention policy, filters by `min_percent`, sorts fullest-first, and falls back to the
assumed EXO default quota (flagged) when ProhibitSendQuota has no explicit value.
"""
import unittest

from execution.core.context import ToolContext


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


def _gb(n):  # an Exchange size string with the "(bytes)" tail the tool parses
    b = n * 1024 ** 3
    return f"{n} GB ({b:,} bytes)"


class FakeEXO:
    """Get-Mailbox returns the listing rows; Get-MailboxStatistics returns a per-mailbox size."""

    def __init__(self, boxes, sizes, archive_sizes=None):
        self.boxes = boxes
        self.sizes = {k.lower(): v for k, v in sizes.items()}
        self.archive_sizes = {k.lower(): v for k, v in (archive_sizes or {}).items()}
        self.calls = []

    def invoke(self, cmdlet, params=None):
        params = params or {}
        self.calls.append((cmdlet, dict(params)))
        if cmdlet == "Get-Mailbox":
            return list(self.boxes)
        if cmdlet == "Get-MailboxStatistics":
            ident = str(params.get("Identity", "")).lower()
            table = self.archive_sizes if params.get("Archive") else self.sizes
            size = table.get(ident)
            return [{"TotalItemSize": size}] if size is not None else {"error": "no stats"}
        return {"error": f"unexpected {cmdlet}"}


def _boxes():
    return [
        {"PrimarySmtpAddress": "full@x.com", "DisplayName": "Full", "RecipientTypeDetails": "UserMailbox",
         "ProhibitSendQuota": _gb(100), "RetentionPolicy": "RHO Executive",
         "ArchiveGuid": "11111111-1111-1111-1111-111111111111", "ArchiveState": "Local"},
        {"PrimarySmtpAddress": "light@x.com", "DisplayName": "Light", "RecipientTypeDetails": "UserMailbox",
         "ProhibitSendQuota": _gb(100), "RetentionPolicy": "Default MRM Policy",
         "ArchiveGuid": "00000000-0000-0000-0000-000000000000", "ArchiveState": "None"},
    ]


class UsageReport(unittest.TestCase):
    def test_reports_percent_archive_and_retention(self):
        from execution.skills import exo_mailbox_usage_report as t
        fake = FakeEXO(_boxes(), sizes={"full@x.com": _gb(95), "light@x.com": _gb(10)},
                       archive_sizes={"full@x.com": _gb(40)})
        r = t.run(_ctx(fake))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["count"], 2)
        by = {row["mailbox"]: row for row in r["mailboxes"]}
        self.assertEqual(by["full@x.com"]["percent_used"], 95.0)
        self.assertEqual(by["full@x.com"]["archive"], "enabled")
        self.assertEqual(by["full@x.com"]["retention_policy"], "RHO Executive")
        self.assertEqual(by["full@x.com"]["archive_usage"], _gb(40))
        self.assertEqual(by["light@x.com"]["percent_used"], 10.0)
        self.assertEqual(by["light@x.com"]["archive"], "disabled")
        self.assertNotIn("archive_usage", by["light@x.com"])  # no archive → no archive stat call

    def test_min_percent_filters_and_sorts_fullest_first(self):
        from execution.skills import exo_mailbox_usage_report as t
        fake = FakeEXO(_boxes(), sizes={"full@x.com": _gb(95), "light@x.com": _gb(10)},
                       archive_sizes={"full@x.com": _gb(40)})
        r = t.run(_ctx(fake), min_percent=90)
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["scanned"], 2)
        self.assertEqual(r["mailboxes"][0]["mailbox"], "full@x.com")

    def test_unlimited_quota_assumes_default_and_flags_it(self):
        from execution.skills import exo_mailbox_usage_report as t
        boxes = [{"PrimarySmtpAddress": "u@x.com", "ProhibitSendQuota": "Unlimited",
                  "ArchiveState": "None"}]
        fake = FakeEXO(boxes, sizes={"u@x.com": _gb(50)})
        r = t.run(_ctx(fake))
        row = r["mailboxes"][0]
        self.assertTrue(row["quota_assumed"])
        self.assertEqual(row["percent_used"], 50.0)  # 50 GB of assumed 100 GB

    def test_type_filter_passes_recipient_type(self):
        from execution.skills import exo_mailbox_usage_report as t
        fake = FakeEXO(_boxes(), sizes={"full@x.com": _gb(95), "light@x.com": _gb(10)},
                       archive_sizes={"full@x.com": _gb(40)})
        t.run(_ctx(fake), type="shared")
        getmb = [c for c in fake.calls if c[0] == "Get-Mailbox"][0]
        self.assertEqual(getmb[1]["RecipientTypeDetails"], "SharedMailbox")


if __name__ == "__main__":
    unittest.main()
