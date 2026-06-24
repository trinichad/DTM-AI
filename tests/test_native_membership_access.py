"""Native bulk/list params on EXO/M365 membership, alias, and mailbox-access skills (D-110).

Each tool gained a list axis so the agent acts on many targets in ONE tool call. These tests
exercise ~5 representative tools: a group-member add (EXO + Graph), an alias add, and a
mailbox-access grant/revoke — asserting batch length, the *_done count, per-row attribution, and
that the single-target path still works. Stateful fakes let the per-item verify-reads succeed.
"""
import unittest

from execution.core.context import ToolContext


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


class StatefulUnifiedEXO:
    """Distribution/unified group whose membership mutates so per-item verify-reads pass."""
    def __init__(self):
        self.members = set()
        self.calls = []

    def invoke(self, cmdlet, params=None):
        params = params or {}
        self.calls.append((cmdlet, params))
        if cmdlet == "Get-DistributionGroup":
            return {"error": "HTTP 404 ManagementObjectNotFound"}   # force the unified path
        if cmdlet == "Get-UnifiedGroup":
            return [{"PrimarySmtpAddress": str(params.get("Identity"))}]
        if cmdlet == "Get-UnifiedGroupLinks":
            return [{"PrimarySmtpAddress": m} for m in sorted(self.members)]
        if cmdlet == "Add-UnifiedGroupLinks":
            self.members.update(params.get("Links") or [])
            return {"ok": True}
        if cmdlet == "Remove-UnifiedGroupLinks":
            for m in params.get("Links") or []:
                self.members.discard(m)
            return {"ok": True}
        return {"error": f"unexpected {cmdlet}"}


class EXOGroupMemberBatch(unittest.TestCase):
    def test_add_group_member_batch_in_one_call(self):
        from execution.skills import exo_add_group_member as ag
        fake = StatefulUnifiedEXO()
        r = ag.run(_ctx(fake), group="team@demodomain.com",
                   members=["a@demodomain.com", "b@demodomain.com"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["members_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual(len(r["results"]), 2)
        self.assertEqual({x["member_added"] for x in r["results"]},
                         {"a@demodomain.com", "b@demodomain.com"})
        self.assertEqual(fake.members, {"a@demodomain.com", "b@demodomain.com"})

    def test_add_group_member_batch_attributes_bad_rows(self):
        from execution.skills import exo_add_group_member as ag
        fake = StatefulUnifiedEXO()
        r = ag.run(_ctx(fake), group="team@demodomain.com",
                   members=["good@demodomain.com", "not-an-email"])
        self.assertTrue(r["ok"], r)                       # at least one succeeded
        self.assertEqual(r["members_done"], 2)
        self.assertEqual(r["ok_count"], 1)
        # every row is attributed to its member (success → member_added, failure → member)
        by = {x.get("member") or x.get("member_added"): x for x in r["results"]}
        self.assertFalse(by["not-an-email"]["ok"])        # attributed to its own member
        self.assertTrue(by["good@demodomain.com"]["ok"])

    def test_add_group_member_single_path_unchanged(self):
        from execution.skills import exo_add_group_member as ag
        fake = StatefulUnifiedEXO()
        r = ag.run(_ctx(fake), group="team@demodomain.com", member="solo@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["member_added"], "solo@demodomain.com")
        self.assertNotIn("members_done", r)               # single path returns the bare _one dict


class FakeGraphGroup:
    """Graph fake: a security group whose membership mutates so verify-reads settle."""
    def __init__(self):
        self.members = set()
        self.users = {"a@x.com": "ua", "b@x.com": "ub", "solo@x.com": "us"}
        self.writes = []

    def get(self, path, params=None):
        if path == "/groups":
            return {"value": [{"id": "g-1", "displayName": "Helpdesk", "groupTypes": [],
                               "onPremisesSyncEnabled": False}]}
        if path.startswith("/groups/") and path.endswith("/members"):
            return {"value": [{"id": uid} for uid in sorted(self.members)]}
        if path.startswith("/users/"):
            upn = path.split("/users/")[1].split("/")[0]
            return ({"id": self.users[upn], "userPrincipalName": upn}
                    if upn in self.users else {"error": "Request_ResourceNotFound"})
        return {"error": f"unexpected GET {path}"}

    def post(self, path, body=None):
        self.writes.append(("POST", path, body))
        # /groups/{id}/members/$ref — pull the uid back out of the @odata.id
        uid = str((body or {}).get("@odata.id", "")).rsplit("/", 1)[-1]
        if uid:
            self.members.add(uid)
        return {"ok": True}


class M365SecurityGroupMemberBatch(unittest.TestCase):
    def test_add_security_group_member_batch_in_one_call(self):
        import os
        os.environ["MSPAI_VERIFY_DELAY"] = "0"
        self.addCleanup(lambda: os.environ.pop("MSPAI_VERIFY_DELAY", None))
        from execution.skills import m365_add_security_group_member as sg
        fake = FakeGraphGroup()
        r = sg.run(_ctx(fake), group="Helpdesk", members=["a@x.com", "b@x.com"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["members_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual({x["member_added"] for x in r["results"]}, {"a@x.com", "b@x.com"})
        self.assertEqual(fake.members, {"ua", "ub"})
        # each member got its own $ref POST — ONE tool call, two writes
        self.assertEqual(len(fake.writes), 2)

    def test_add_security_group_member_single_path_unchanged(self):
        import os
        os.environ["MSPAI_VERIFY_DELAY"] = "0"
        self.addCleanup(lambda: os.environ.pop("MSPAI_VERIFY_DELAY", None))
        from execution.skills import m365_add_security_group_member as sg
        fake = FakeGraphGroup()
        r = sg.run(_ctx(fake), group="Helpdesk", member="solo@x.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["member_added"], "solo@x.com")
        self.assertNotIn("members_done", r)


class StatefulAliasEXO:
    """One mailbox whose EmailAddresses list mutates so the alias verify-read passes."""
    def __init__(self, primary="user@demodomain.com"):
        self.primary = primary
        self.addresses = [f"SMTP:{primary}"]
        self.calls = []

    def invoke(self, cmdlet, params=None):
        params = params or {}
        self.calls.append((cmdlet, params))
        if cmdlet == "Get-Mailbox":
            return [{"PrimarySmtpAddress": self.primary,
                     "RecipientTypeDetails": "UserMailbox",
                     "IsDirSynced": False, "IsExchangeCloudManaged": True,
                     "EmailAddresses": list(self.addresses)}]
        if cmdlet == "Set-Mailbox":
            ht = params.get("EmailAddresses") or {}
            if ht.get("Add"):
                self.addresses.append(ht["Add"])
            if ht.get("Remove") and ht["Remove"] in self.addresses:
                self.addresses.remove(ht["Remove"])
            return {"ok": True}
        return {"error": f"unexpected {cmdlet}"}


class EXOAliasBatch(unittest.TestCase):
    def test_add_alias_batch_in_one_call(self):
        from execution.skills import exo_add_alias as aa
        fake = StatefulAliasEXO()
        r = aa.run(_ctx(fake), identity="user@demodomain.com",
                   aliases=["sales@demodomain.com", "info@demodomain.com"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["aliases_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual({x["alias_added"] for x in r["results"]},
                         {"sales@demodomain.com", "info@demodomain.com"})
        self.assertIn("smtp:sales@demodomain.com", [a.lower() for a in fake.addresses])
        self.assertIn("smtp:info@demodomain.com", [a.lower() for a in fake.addresses])

    def test_add_alias_single_path_unchanged(self):
        from execution.skills import exo_add_alias as aa
        fake = StatefulAliasEXO()
        r = aa.run(_ctx(fake), identity="user@demodomain.com", alias="only@demodomain.com")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["alias_added"], "only@demodomain.com")
        self.assertNotIn("aliases_done", r)


class StatefulMailboxAccessEXO:
    """Tracks Full Access trustees on one mailbox so grant/revoke verify-reads settle."""
    def __init__(self, mailbox="shared@demodomain.com"):
        self.mailbox = mailbox
        self.full_access = set()
        self.calls = []

    def invoke(self, cmdlet, params=None):
        params = params or {}
        self.calls.append((cmdlet, params))
        if cmdlet == "Get-Mailbox":
            return [{"PrimarySmtpAddress": self.mailbox, "RecipientTypeDetails": "SharedMailbox",
                     "GrantSendOnBehalfTo": []}]
        if cmdlet == "Get-MailboxPermission":
            return [{"User": u, "AccessRights": ["FullAccess"]} for u in sorted(self.full_access)]
        if cmdlet == "Add-MailboxPermission":
            self.full_access.add(params.get("User"))
            return {"ok": True}
        if cmdlet == "Remove-MailboxPermission":
            self.full_access.discard(params.get("User"))
            return {"ok": True}
        return {"error": f"unexpected {cmdlet}"}


class EXOMailboxAccessBatch(unittest.TestCase):
    def test_grant_mailbox_access_batch_in_one_call(self):
        from execution.skills import exo_grant_mailbox_access as gm
        fake = StatefulMailboxAccessEXO()
        r = gm.run(_ctx(fake), mailbox="shared@demodomain.com", access="full_access",
                   users=["a@demodomain.com", "b@demodomain.com"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["users_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual(r["access"], "full_access")
        self.assertEqual({x["user"] for x in r["results"]},
                         {"a@demodomain.com", "b@demodomain.com"})
        self.assertEqual(fake.full_access, {"a@demodomain.com", "b@demodomain.com"})
        # the mailbox preflight (Get-Mailbox) ran exactly once for the whole batch
        self.assertEqual(sum(1 for c in fake.calls if c[0] == "Get-Mailbox"
                             and c[1].get("Identity") == "shared@demodomain.com"
                             and "GrantSendOnBehalfTo" not in str(c)), 1)

    def test_grant_mailbox_access_single_path_unchanged(self):
        from execution.skills import exo_grant_mailbox_access as gm
        fake = StatefulMailboxAccessEXO()
        r = gm.run(_ctx(fake), mailbox="shared@demodomain.com",
                   user="solo@demodomain.com", access="full_access")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["user"], "solo@demodomain.com")
        self.assertEqual(r["access"], "full_access")
        self.assertNotIn("users_done", r)

    def test_revoke_mailbox_access_batch_in_one_call(self):
        from execution.skills import exo_revoke_mailbox_access as rm
        fake = StatefulMailboxAccessEXO()
        fake.full_access = {"a@demodomain.com", "b@demodomain.com"}
        r = rm.run(_ctx(fake), mailbox="shared@demodomain.com", access="full_access",
                   users=["a@demodomain.com", "b@demodomain.com"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["users_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual(r["access"], "full_access")
        self.assertEqual({x["user"] for x in r["results"]},
                         {"a@demodomain.com", "b@demodomain.com"})
        self.assertEqual(fake.full_access, set())          # both removed

    def test_revoke_mailbox_access_single_path_unchanged(self):
        from execution.skills import exo_revoke_mailbox_access as rm
        fake = StatefulMailboxAccessEXO()
        fake.full_access = {"solo@demodomain.com"}
        r = rm.run(_ctx(fake), mailbox="shared@demodomain.com",
                   user="solo@demodomain.com", access="full_access")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["access_revoked"], "full_access")
        self.assertNotIn("users_done", r)


if __name__ == "__main__":
    unittest.main()
