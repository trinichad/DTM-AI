"""Native bulk/list params on the per-mailbox EXO skills (D-110).

Each tool gained an array param (`identities` / `users` / `mailboxes`) so the agent can act on
MANY mailboxes in ONE tool call. These tests prove: the list path returns a per-row results list
with attribution + a *_done count, and the single path is unchanged. The fakes are keyed by
Identity so each item's verify-read (the D-43 read-your-write check) succeeds independently.
"""
import unittest

from execution.core.context import ToolContext


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


class StatefulEXO:
    """EXO fake keyed by lowercased Identity — mutates on Set-* so verify re-reads see the change.

    `boxes` maps address -> mailbox dict. Folder-permission and junk reads draw from optional
    per-address tables so a tool's full verify sequence works for every item in a batch.
    """

    def __init__(self, boxes, *, folder_perms=None, junk=None):
        self.boxes = {k.lower(): v for k, v in boxes.items()}
        self.folder_perms = {k.lower(): v for k, v in (folder_perms or {}).items()}
        self.junk = {k.lower(): v for k, v in (junk or {}).items()}
        self.calls = []

    def _mb(self, ident):
        return self.boxes.get(str(ident).lower())

    def invoke(self, cmdlet, params=None):
        params = params or {}
        self.calls.append((cmdlet, dict(params)))
        ident = str(params.get("Identity", "")).split(":")[0].lower()

        if cmdlet == "Get-Mailbox":
            mb = self._mb(ident)
            if not ident:                                # whole-tenant listing
                return list(self.boxes.values())
            return [mb] if mb else {"error": "couldn't be found"}

        if cmdlet == "Set-Mailbox":
            mb = self._mb(ident)
            if mb is None:
                return {"error": "couldn't be found"}
            for k, v in params.items():
                if k in ("Identity", "Confirm"):
                    continue
                # Exchange echoes ForwardingSmtpAddress back with an smtp: prefix (D-55)
                if k == "ForwardingSmtpAddress" and v:
                    v = f"smtp:{v}"
                mb[k] = v
            return {"ok": True}

        if cmdlet in ("Enable-Mailbox", "Disable-Mailbox"):
            mb = self._mb(ident)
            if mb is None:
                return {"error": "couldn't be found"}
            if cmdlet == "Enable-Mailbox":
                mb["ArchiveGuid"] = "11111111-1111-1111-1111-111111111111"
                mb["ArchiveState"] = "Local"
            else:
                mb["ArchiveGuid"] = "00000000-0000-0000-0000-000000000000"
                mb["ArchiveState"] = "None"
            return {"ok": True}

        if cmdlet == "Get-MailboxFolderPermission":
            return self.folder_perms.get(ident, [])

        if cmdlet == "Get-MailboxPermission":
            return []

        if cmdlet == "Get-RecipientPermission":
            return []

        if cmdlet == "Get-MailboxJunkEmailConfiguration":
            return self.junk.get(ident, [])

        if cmdlet == "Get-Recipient":
            return []

        return {"error": f"unexpected cmdlet {cmdlet}"}


# ───────────────────────── writes ─────────────────────────

class ArchiveBulk(unittest.TestCase):
    def _boxes(self):
        return {"a@x.com": {"PrimarySmtpAddress": "a@x.com",
                            "ArchiveGuid": "00000000-0000-0000-0000-000000000000",
                            "ArchiveState": "None"},
                "b@x.com": {"PrimarySmtpAddress": "b@x.com",
                            "ArchiveGuid": "00000000-0000-0000-0000-000000000000",
                            "ArchiveState": "None"}}

    def test_list_enables_each_and_attributes_rows(self):
        from execution.skills import exo_set_archive
        fake = StatefulEXO(self._boxes())
        r = exo_set_archive.run(_ctx(fake), identities=["a@x.com", "b@x.com"], enabled=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["archive_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual(len(r["results"]), 2)
        by = {row["identity"]: row for row in r["results"]}
        self.assertEqual(by["a@x.com"]["archive"], "enabled")
        self.assertEqual(by["b@x.com"]["archive"], "enabled")

    def test_missing_mailbox_in_list_is_a_failed_row_not_a_raise(self):
        from execution.skills import exo_set_archive
        fake = StatefulEXO(self._boxes())
        r = exo_set_archive.run(_ctx(fake), identities=["a@x.com", "ghost@x.com"], enabled=True)
        self.assertTrue(r["ok"])                          # at least one succeeded
        self.assertEqual(r["ok_count"], 1)
        by = {row["identity"]: row for row in r["results"]}
        self.assertFalse(by["ghost@x.com"]["ok"])
        self.assertIn("ghost@x.com", by["ghost@x.com"]["identity"])

    def test_single_path_unchanged(self):
        from execution.skills import exo_set_archive
        fake = StatefulEXO(self._boxes())
        r = exo_set_archive.run(_ctx(fake), identity="a@x.com", enabled=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["archive"], "enabled")
        self.assertNotIn("results", r)                    # single returns the flat dict


class ForwardingBulk(unittest.TestCase):
    def _boxes(self):
        return {"a@x.com": {"PrimarySmtpAddress": "a@x.com", "IsDirSynced": False},
                "b@x.com": {"PrimarySmtpAddress": "b@x.com", "IsDirSynced": False}}

    def test_list_sets_forwarding_and_verifies_each(self):
        from execution.skills import exo_set_forwarding
        fake = StatefulEXO(self._boxes())
        r = exo_set_forwarding.run(_ctx(fake), identities=["a@x.com", "b@x.com"],
                                   forward_to="boss@x.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["forwarding_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        for row in r["results"]:
            self.assertTrue(row["ok"], row)
            self.assertIn(row["identity"], ("a@x.com", "b@x.com"))

    def test_single_path_unchanged(self):
        from execution.skills import exo_set_forwarding
        fake = StatefulEXO(self._boxes())
        r = exo_set_forwarding.run(_ctx(fake), identity="a@x.com", forward_to="boss@x.com")
        self.assertTrue(r["ok"], r)
        self.assertIn("forwarding to boss@x.com", r["note"])
        self.assertNotIn("results", r)


class JunkFilterBulk(unittest.TestCase):
    def _fake(self):
        boxes = {"a@x.com": {"PrimarySmtpAddress": "a@x.com"},
                 "b@x.com": {"PrimarySmtpAddress": "b@x.com"}}
        # both currently ON; turning OFF must flip + verify
        junk = {"a@x.com": [{"Enabled": True}], "b@x.com": [{"Enabled": True}]}
        return StatefulEXO(boxes, junk=junk)

    def test_list_disables_each(self):
        from execution.skills import exo_set_junk_filter
        fake = self._fake()
        # Set-MailboxJunkEmailConfiguration mutates the junk table so the verify read sees it
        orig = fake.invoke

        def invoke(cmdlet, params=None):
            params = params or {}
            if cmdlet == "Set-MailboxJunkEmailConfiguration":
                fake.calls.append((cmdlet, dict(params)))
                ident = str(params.get("Identity", "")).lower()
                fake.junk[ident] = [{"Enabled": bool(params.get("Enabled"))}]
                return {"ok": True}
            return orig(cmdlet, params)
        fake.invoke = invoke
        r = exo_set_junk_filter.run(_ctx(fake), identities=["a@x.com", "b@x.com"], enabled=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["junk_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual({row["identity"] for row in r["results"]}, {"a@x.com", "b@x.com"})


# ───────────────────────── reads ─────────────────────────

class MailboxPermissionsBulk(unittest.TestCase):
    def _boxes(self):
        return {"a@x.com": {"PrimarySmtpAddress": "a@x.com", "DisplayName": "A",
                            "RecipientTypeDetails": "SharedMailbox"},
                "b@x.com": {"PrimarySmtpAddress": "b@x.com", "DisplayName": "B",
                            "RecipientTypeDetails": "SharedMailbox"}}

    def test_list_checks_each_and_attributes(self):
        from execution.skills import exo_mailbox_permissions
        fake = StatefulEXO(self._boxes())
        r = exo_mailbox_permissions.run(_ctx(fake), identities=["a@x.com", "b@x.com"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["mailboxes_checked"], 2)
        self.assertEqual(len(r["results"]), 2)
        by = {row["identity"]: row for row in r["results"]}
        self.assertEqual(by["a@x.com"]["mailbox"], "a@x.com")
        self.assertEqual(by["b@x.com"]["type"], "SharedMailbox")

    def test_single_path_unchanged(self):
        from execution.skills import exo_mailbox_permissions
        fake = StatefulEXO(self._boxes())
        r = exo_mailbox_permissions.run(_ctx(fake), identity="a@x.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["mailbox"], "a@x.com")
        self.assertNotIn("results", r)


class FolderPermissionsBulk(unittest.TestCase):
    def _fake(self):
        boxes = {"a@x.com": {"PrimarySmtpAddress": "a@x.com", "DisplayName": "A"},
                 "b@x.com": {"PrimarySmtpAddress": "b@x.com", "DisplayName": "B"}}
        perms = {
            "a@x.com": [{"User": "Default", "AccessRights": ["AvailabilityOnly"]},
                        {"User": "Bob", "AccessRights": ["Reviewer"]}],
            "b@x.com": [{"User": "Default", "AccessRights": ["AvailabilityOnly"]}]}
        return StatefulEXO(boxes, folder_perms=perms)

    def test_list_reports_each_mailbox(self):
        from execution.skills import exo_folder_permissions
        fake = self._fake()
        r = exo_folder_permissions.run(_ctx(fake), mailboxes=["a@x.com", "b@x.com"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["mailboxes_checked"], 2)
        by = {row["mailbox"]: row for row in r["results"]}
        self.assertEqual(by["a@x.com"]["count"], 2)
        self.assertEqual(by["b@x.com"]["count"], 1)

    def test_single_path_unchanged(self):
        from execution.skills import exo_folder_permissions
        fake = self._fake()
        r = exo_folder_permissions.run(_ctx(fake), mailbox="a@x.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["mailbox"], "a@x.com")
        self.assertNotIn("results", r)


class UserDistributionGroupsBulk(unittest.TestCase):
    def _fake(self):
        # each user resolves to a mailbox with a DistinguishedName; Get-Recipient returns no
        # groups (empty membership) — enough to prove batch attribution + the *_checked count.
        boxes = {"a@x.com": {"PrimarySmtpAddress": "a@x.com", "DistinguishedName": "CN=A"},
                 "b@x.com": {"PrimarySmtpAddress": "b@x.com", "DistinguishedName": "CN=B"}}
        return StatefulEXO(boxes)

    def test_list_checks_each_user(self):
        from execution.skills import exo_user_distribution_groups
        fake = self._fake()
        r = exo_user_distribution_groups.run(_ctx(fake), users=["a@x.com", "b@x.com"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["users_checked"], 2)
        self.assertEqual({row["user"] for row in r["results"]}, {"a@x.com", "b@x.com"})
        for row in r["results"]:
            self.assertTrue(row["ok"], row)
            self.assertEqual(row["count"], 0)

    def test_single_path_unchanged(self):
        from execution.skills import exo_user_distribution_groups
        fake = self._fake()
        r = exo_user_distribution_groups.run(_ctx(fake), user="a@x.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["user"], "a@x.com")
        self.assertNotIn("results", r)


if __name__ == "__main__":
    unittest.main()
