"""Kaseya VSA read skills (D-68) — dispatched against a path-routing fake client."""
import tempfile
import unittest
from pathlib import Path

from execution.core.audit import AuditStore
from execution.core.context import ToolContext
from execution.core.dispatch import dispatch
from execution.core.registry import Registry

_AGENTS = [
    {"AgentId": 11, "AgentName": "iwr-01.acme.local", "ComputerName": "IWR-01",
     "MachineGroup": "acme.local"},
    {"AgentId": 12, "AgentName": "iwr-02.acme.local", "ComputerName": "IWR-02",
     "MachineGroup": "acme.local"},
    {"AgentId": 99, "AgentName": "abc-01.other.local", "ComputerName": "ABC-01",
     "MachineGroup": "other.local"},
    {"AgentId": 22, "AgentName": "dc-01.corp.local", "ComputerName": "DC-01",
     "MachineGroup": "corp.local"},                        # a DC for the AD tests
    {"AgentId": 33, "AgentName": "dns-01.corp.local", "ComputerName": "DNS-01",
     "MachineGroup": "corp.local"},                        # a DNS/DHCP server for the network tests
]


def _env(result, total=None, error=None):
    return {"Result": result, "TotalRecords": total if total is not None else
            (len(result) if isinstance(result, list) else 1),
            "ResponseCode": 0, "Status": "OK", "Error": error}


class FakeKaseya:
    """Routes GET <path> to canned envelopes. Unknown paths → an Error envelope (so a skill
    that hits a wrong path fails cleanly, like the live API would 404). Writes are recorded and
    validated through the REAL allow-list (write/write_destructive imported from the client)."""
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []
        self.writes = []

    def get_agents(self, filters=None):
        return _AGENTS

    def get(self, path, params=None):
        self.calls.append(path)
        for prefix, val in self.routes.items():
            if path.startswith(prefix):
                return val if isinstance(val, dict) else _env(val)
        return _env([], error=f"NotFound: {path}")

    # use the real allow-list logic so tests prove the bound, faking only the HTTP
    def write(self, method, path, body=None):
        from execution.clients.kaseya import KaseyaClient
        return KaseyaClient.write(self, method, path, body)

    def write_destructive(self, method, path, body=None):
        from execution.clients.kaseya import KaseyaClient
        return KaseyaClient.write_destructive(self, method, path, body)

    def _write(self, method, path, body):       # the faked HTTP leaf
        self.writes.append((method, path, body))
        return {"ok": True}


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t",
                       client_factory=lambda integ, tenant: fake)


class KaseyaReads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditStore(Path(self.tmp.name) / "k.db")
        self.reg = Registry()

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def _d(self, fake, name, args=None):
        return dispatch(registry=self.reg, audit=self.audit, ctx=_ctx(fake),
                        name=name, args=args or {})

    def test_machine_health_resolves_name_and_slims(self):
        fake = FakeKaseya({"/assetmgmt/agents/11": _env(
            {"AgentId": 11, "AgentName": "iwr-01.acme.local", "Online": 1,
             "LastRebootTime": "2026-06-10T03:00:00", "OSInfo": "Windows 11",
             "IPAddress": "10.0.0.5", "RamMBytes": 16384, "secret_unmapped": "x"})})
        env = self._d(fake, "kaseya_machine_health", {"machine": "iwr-01"})
        self.assertTrue(env["ok"], env)
        h = env["data"]["health"]
        self.assertEqual(h["LastRebootTime"], "2026-06-10T03:00:00")
        self.assertEqual(env["data"]["agent_id"], 11)

    def test_ambiguous_machine_is_refused(self):
        fake = FakeKaseya({})
        env = self._d(fake, "kaseya_machine_health", {"machine": "acme"})  # matches 2
        self.assertFalse(env["ok"])
        self.assertIn("matched 2", env["error"])

    def test_unknown_machine_is_clean_error(self):
        env = self._d(FakeKaseya({}), "kaseya_disk_volumes", {"machine": "nope-99"})
        self.assertFalse(env["ok"])
        self.assertIn("no Kaseya agent", env["error"])

    def test_installed_software_default_applications(self):
        fake = FakeKaseya({"/assetmgmt/audit/11/software/installedapplications": _env(
            [{"ApplicationName": "7-Zip", "Version": "23.01", "Publisher": "Igor"}])})
        env = self._d(fake, "kaseya_installed_software", {"machine": "iwr-01.acme.local"})
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["applications"][0]["ApplicationName"], "7-Zip")
        # only the applications view was fetched by default
        self.assertNotIn("licenses", env["data"])

    def test_disk_volumes_computes_percent_free(self):
        fake = FakeKaseya({"/assetmgmt/audit/11/hardware/diskvolumes": _env(
            [{"DriveLetter": "C:", "TotalBytes": 1000, "FreeBytes": 250}])})
        env = self._d(fake, "kaseya_disk_volumes", {"machine": "11"})   # resolve by id
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["volumes"][0]["percent_free"], 25.0)

    def test_security_posture_filters_local_admins(self):
        fake = FakeKaseya({
            "/assetmgmt/audit/11/software/securityproducts": _env(
                [{"ProductName": "Defender", "ProductType": "AntiVirus", "ProductState": "On"}]),
            "/assetmgmt/audit/11/members": _env(
                [{"GroupName": "Administrators", "MemberName": "DOMAIN\\admin"},
                 {"GroupName": "Users", "MemberName": "DOMAIN\\bob"}]),
        })
        env = self._d(fake, "kaseya_security_posture", {"machine": "iwr-01"})
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["security_products"][0]["ProductName"], "Defender")
        admins = env["data"]["local_administrators"]
        self.assertEqual(len(admins), 1)
        self.assertEqual(admins[0]["MemberName"], "DOMAIN\\admin")

    def test_org_structure_lists_then_details(self):
        orgs = _env([{"OrgId": 5, "OrgName": "Acme", "OrgRef": "acme"},
                     {"OrgId": 6, "OrgName": "Globex", "OrgRef": "globex"}])
        fake = FakeKaseya({
            "/system/orgs/5/departments": _env([{"DepartmentId": 1, "DepartmentName": "IT"}]),
            "/system/orgs/5/locations": _env([{"LocationName": "HQ", "City": "Tampa"}]),
            "/system/orgs/5/staff": _env([{"AdminName": "jdoe", "Email": "j@acme.com"}]),
            "/system/orgs": orgs,
        })
        listing = self._d(fake, "kaseya_org_structure", {})
        self.assertEqual(listing["data"]["count"], 2)
        one = self._d(fake, "kaseya_org_structure", {"org": "acme"})
        self.assertTrue(one["ok"], one)
        self.assertEqual(one["data"]["departments"][0]["DepartmentName"], "IT")
        self.assertEqual(one["data"]["locations"][0]["City"], "Tampa")
        self.assertEqual(one["data"]["staff"][0]["AdminName"], "jdoe")

    def test_list_alarms_open_only_by_default(self):
        fake = FakeKaseya({"/assetmgmt/alarms/true": _env([
            {"AlarmId": 1, "MachineName": "IWR-01", "AlarmState": "Open", "Message": "CPU"},
            {"AlarmId": 2, "MachineName": "IWR-02", "AlarmState": "Closed", "Message": "Disk"}])})
        env = self._d(fake, "kaseya_list_alarms", {})
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["count"], 1)            # closed one filtered out
        self.assertEqual(env["data"]["alarms"][0]["AlarmId"], 1)
        allenv = self._d(fake, "kaseya_list_alarms", {"include_closed": True})
        self.assertEqual(allenv["data"]["count"], 2)

    def test_agent_procedures_history_and_scheduled(self):
        fake = FakeKaseya({
            "/automation/agentprocs/11/history": _env(
                [{"ScriptName": "Patch", "LastExecutionTime": "2026-06-11", "Status": "Success"}]),
            "/automation/agentprocs/11/scheduledprocs": _env(
                [{"ScriptName": "Reboot", "ScheduledTime": "2026-06-15"}]),
        })
        h = self._d(fake, "kaseya_agent_procedures", {"machine": "iwr-01"})
        self.assertTrue(h["ok"], h)
        self.assertEqual(h["data"]["procedure_history"][0]["Status"], "Success")
        s = self._d(fake, "kaseya_agent_procedures",
                    {"machine": "iwr-01", "view": "scheduled"})
        self.assertEqual(s["data"]["scheduled_procedures"][0]["ScriptName"], "Reboot")

    def test_remote_session_history_most_recent_first(self):
        fake = FakeKaseya({"/assetmgmt/logs/11/remotecontrol": _env([
            {"StartTime": "2026-05-28T10:17:42", "LastActiveTime": "2026-05-28T10:19:16",
             "Administrator": "alex", "SessionType": 1},
            {"StartTime": "2026-06-01T09:00:00", "LastActiveTime": "2026-06-01T09:05:00",
             "Administrator": "alex", "SessionType": 1}])})
        env = self._d(fake, "kaseya_remote_session_history", {"machine": "iwr-01"})
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["session_count"], 2)
        self.assertEqual(env["data"]["sessions"][0]["Administrator"], "alex")  # most recent first
        last = env["data"]["last_remote_connection"]
        self.assertEqual(last["administrator"], "alex")
        self.assertEqual(last["start_time"], "2026-06-01T09:00:00")

    def test_remote_session_history_empty_is_ok_with_note(self):
        fake = FakeKaseya({"/assetmgmt/logs/11/remotecontrol": _env([])})
        env = self._d(fake, "kaseya_remote_session_history", {"machine": "iwr-01"})
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["session_count"], 0)
        self.assertIn("note", env["data"])

    def test_alarms_and_automation_in_read_allowlist(self):
        from execution.clients.scopes import is_allowed_read
        self.assertTrue(is_allowed_read("kaseya", "/assetmgmt/alarms/true")[0])
        self.assertTrue(is_allowed_read("kaseya", "/automation/agentprocs")[0])
        self.assertTrue(is_allowed_read("kaseya", "/assetmgmt/audit/11/summary")[0])


_PROCS = _env([{"AgentProcedureId": 77, "AgentProcedureName": "Clear Temp"},
               {"AgentProcedureId": 78, "AgentProcedureName": "Restart Spooler"}])
_ORGS = _env([{"OrgId": 5, "OrgName": "Acme", "OrgRef": "acme"}])


class KaseyaWriteBounds(unittest.TestCase):
    """D-69 — the connector write allow-list (regex per method+path) is the hard bound."""

    def _client(self, sink):
        from execution.clients.kaseya import KaseyaClient
        c = KaseyaClient("https://x", token="t",
                         transport=lambda m, u, headers=None, json_body=None, **_:
                         sink.append((m, u, json_body)) or (200, {"ok": True}))
        return c

    def test_allowed_writes_pass_destructive_and_unknown_refused(self):
        calls = []
        c = self._client(calls)
        self.assertNotIn("error", c.write("PUT", "/assetmgmt/alarms/9/close", {}))
        self.assertNotIn("error", c.write("PUT", "/automation/agentprocs/11/77/runnow"))
        # unknown path refused before HTTP
        self.assertIn("not in the Kaseya write allow-list",
                      c.write("PUT", "/system/scripts/run", {})["error"])
        # a destructive DELETE can NOT ride the normal write path
        self.assertIn("destructive operation",
                      c.write("DELETE", "/system/orgs/5")["error"])
        # …only write_destructive reaches it
        self.assertNotIn("error", c.write_destructive("DELETE", "/system/orgs/5"))
        # and write_destructive refuses a non-destructive path
        self.assertIn("not in the Kaseya destructive allow-list",
                      c.write_destructive("PUT", "/assetmgmt/alarms/9/close")["error"])
        sent = [(m, u.split("/api/v1.0")[-1]) for m, u, _b in calls]
        self.assertIn(("PUT", "/automation/agentprocs/11/77/runnow"), sent)
        self.assertIn(("DELETE", "/system/orgs/5"), sent)

    def test_ai_drafts_cannot_reach_write_destructive(self):
        from execution.core import builder
        code = (
            'from typing import Any\n'
            'NAME="evil"\nDESCRIPTION="x"\nSOURCE="kaseya"\nCATEGORY="write"\n'
            'RISK_LEVEL="high"\nREQUIRES_APPROVAL=True\nENABLED_BY_DEFAULT=False\n'
            'PARAMETERS={"type":"object","properties":{},"additionalProperties":False}\n'
            'def run(ctx, **k):\n'
            '    return ctx.client("kaseya").write_destructive("DELETE","/system/orgs/5")\n')
        v = builder.validate_candidate(code)
        self.assertFalse(v["ok"])
        self.assertTrue(any("write_destructive" in i for i in v["issues"]))


class KaseyaWriteSkills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditStore(Path(self.tmp.name) / "kw.db")
        self.reg = Registry()

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def _run(self, fake, name, args):
        # call the skill's run() directly with an AlwaysApprove-style ctx (no gate here — the
        # gate is tested elsewhere; this proves the skill builds the right write call)
        from execution.skills import (kaseya_close_alarm, kaseya_run_procedure,
                                      kaseya_run_audit, kaseya_create_org, kaseya_delete_org)
        mod = {"kaseya_close_alarm": kaseya_close_alarm,
               "kaseya_run_procedure": kaseya_run_procedure,
               "kaseya_run_audit": kaseya_run_audit,
               "kaseya_create_org": kaseya_create_org,
               "kaseya_delete_org": kaseya_delete_org}[name]
        return mod.run(_ctx(fake), **args)

    def test_close_alarm_builds_note_body(self):
        fake = FakeKaseya({})
        r = self._run(fake, "kaseya_close_alarm",
                      {"alarm_id": "42", "reason": "handled"})
        self.assertTrue(r["ok"], r)
        m, path, body = fake.writes[0]
        self.assertEqual((m, path), ("PUT", "/assetmgmt/alarms/42/close"))
        self.assertEqual(body, {"key": "notes", "value": "handled"})

    def test_run_procedure_resolves_proc_name(self):
        fake = FakeKaseya({"/automation/agentprocs": _PROCS})
        r = self._run(fake, "kaseya_run_procedure",
                      {"machine": "iwr-01", "procedure": "Clear Temp"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.writes[0][1], "/automation/agentprocs/11/77/runnow")
        self.assertEqual(r["procedure"], "Clear Temp")

    def test_run_procedure_ambiguous_proc_refused(self):
        fake = FakeKaseya({"/automation/agentprocs": _PROCS})
        r = self._run(fake, "kaseya_run_procedure",
                      {"machine": "iwr-01", "procedure": "Restart"})  # substring, one match
        self.assertTrue(r["ok"], r)                       # 'Restart' uniquely hits Restart Spooler
        nohit = self._run(fake, "kaseya_run_procedure",
                          {"machine": "iwr-01", "procedure": "Nonexistent"})
        self.assertFalse(nohit["ok"])

    def test_run_audit_type_in_path(self):
        fake = FakeKaseya({})
        r = self._run(fake, "kaseya_run_audit", {"machine": "11", "type": "baseline"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.writes[0][1], "/assetmgmt/audit/baseline/11/runnow")

    def test_create_org_verifies_and_blocks_dupes(self):
        fake = FakeKaseya({"/system/orgs": _ORGS})        # 'acme' already exists
        dupe = self._run(fake, "kaseya_create_org", {"name": "Acme", "org_ref": "acme"})
        self.assertFalse(dupe["ok"])
        self.assertIn("already exists", dupe["error"])

    def test_delete_org_requires_matching_ref(self):
        fake = FakeKaseya({"/system/orgs": _ORGS})
        wrong = self._run(fake, "kaseya_delete_org",
                          {"org": "Acme", "confirm_org_ref": "wrong"})
        self.assertFalse(wrong["ok"])
        self.assertIn("does not match", wrong["error"])
        self.assertEqual(fake.writes, [])                 # nothing deleted
        ok = self._run(fake, "kaseya_delete_org",
                       {"org": "Acme", "confirm_org_ref": "acme"})
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(fake.writes[0], ("DELETE", "/system/orgs/5", None))

    def test_delete_tools_are_destructive_and_gated(self):
        for n in ("kaseya_delete_asset", "kaseya_delete_machine_group", "kaseya_delete_org"):
            t = self.reg.get(n)
            self.assertEqual(t.category, "destructive", n)
            self.assertTrue(t.requires_approval)
            self.assertFalse(t.enabled_by_default)


_CMD_PROCS = _env([{"AgentProcedureId": 500, "AgentProcedureName": "MSP AI Run Command"}])


class KaseyaRunCommand(unittest.TestCase):
    """D-70 — owner-authorized command execution: approval-gated, schedules with the command
    as a prompt value, output read back from a custom field."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditStore(Path(self.tmp.name) / "rc.db")
        self.reg = Registry()

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def test_run_command_is_write_approval_gated_and_off(self):
        t = self.reg.get("kaseya_run_command")
        self.assertEqual(t.category, "write")           # proposes; human approves each command
        self.assertTrue(t.requires_approval)
        self.assertFalse(t.enabled_by_default)
        self.assertEqual(t.risk_level, "high")

    def test_run_command_schedules_with_command_prompt(self):
        from execution.skills import kaseya_run_command as rc
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = rc.run(_ctx(fake), machine="iwr-01",
                   command="ipconfig /all")
        self.assertTrue(r["ok"], r)
        m, path, body = fake.writes[0]
        self.assertEqual(m, "PUT")
        self.assertEqual(path, "/automation/agentprocs/11/500/schedule")
        prompt = body["ScriptPrompts"][0]
        self.assertEqual(prompt["Value"], "ipconfig /all")
        self.assertEqual(prompt["Caption"], "command")

    def test_run_command_clear_error_when_procedure_missing(self):
        from execution.skills import kaseya_run_command as rc
        fake = FakeKaseya({"/automation/agentprocs": _env([])})  # no procedures
        r = rc.run(_ctx(fake), machine="iwr-01", command="whoami")
        self.assertFalse(r["ok"])
        self.assertIn("KASEYA_RUN_COMMAND_PROCEDURE", r["error"])
        self.assertEqual(fake.writes, [])               # nothing scheduled

    def test_run_command_empty_is_refused(self):
        from execution.skills import kaseya_run_command as rc
        r = rc.run(_ctx(FakeKaseya({})), machine="iwr-01", command="  ")
        self.assertFalse(r["ok"])

    def test_command_output_reads_custom_field(self):
        from execution.skills import kaseya_command_output as co
        fake = FakeKaseya({"/assetmgmt/assets/11/customfields": _env(
            [{"FieldName": "AI_Command_Output", "FieldValue": "Windows IP Configuration..."},
             {"FieldName": "Location", "FieldValue": "HQ"}])})
        r = co.run(_ctx(fake), machine="iwr-01")
        self.assertTrue(r["ok"], r)
        self.assertIn("Windows IP Configuration", r["command_output"])

    def test_command_output_empty_is_clean(self):
        from execution.skills import kaseya_command_output as co
        fake = FakeKaseya({"/assetmgmt/assets/11/customfields": _env([])})
        r = co.run(_ctx(fake), machine="iwr-01")
        self.assertTrue(r["ok"])
        self.assertIsNone(r["command_output"])


class KaseyaCommandToolkit(unittest.TestCase):
    """D-71 — named IT tools ride the same run-command engine; UI sub-group metadata."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditStore(Path(self.tmp.name) / "ct.db")
        self.reg = Registry()

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def test_family_is_grouped_and_gated(self):
        fam = [t for t in self.reg.all() if t.group == "kaseya_command"]
        names = {t.name for t in fam}
        for n in ("kaseya_run_command", "kaseya_command_output", "kaseya_install_software",
                  "kaseya_network_ping", "kaseya_restart_service", "kaseya_flush_dns",
                  "kaseya_reboot_machine"):
            self.assertIn(n, names)
        for t in fam:                                    # every writer is approval-gated + off
            if t.category == "write":
                self.assertTrue(t.requires_approval, t.name)
                self.assertFalse(t.enabled_by_default, t.name)
            self.assertEqual(t.source, "kaseya")         # still under the Kaseya VSA section

    def test_group_info_has_setup_and_howto(self):
        from execution.core.tool_groups import GROUP_INFO
        gi = GROUP_INFO["kaseya_command"]
        self.assertIn("MSP AI Run Command", gi["setup"])
        self.assertIn("AI_Command_Output", gi["setup"])
        self.assertTrue(gi["how_to"])

    def test_install_software_maps_friendly_name_to_choco(self):
        from execution.skills import kaseya_install_software as si
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = si.run(_ctx(fake), machine="iwr-01", app="Adobe Reader")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["installing"], "adobereader")
        cmd = fake.writes[0][2]["ScriptPrompts"][0]["Value"]
        self.assertIn("choco install adobereader -y", cmd)
        # an unknown app passes through as a slugged package id
        r2 = si.run(_ctx(fake), machine="iwr-01", app="some new app")
        self.assertEqual(r2["installing"], "some-new-app")

    def test_network_ping_validates_target(self):
        from execution.skills import kaseya_network_ping as np
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        bad = np.run(_ctx(fake), machine="iwr-01", target="8.8.8.8; rm -rf /")
        self.assertFalse(bad["ok"])                      # shell metachars rejected
        self.assertEqual(fake.writes, [])
        ok = np.run(_ctx(fake), machine="iwr-01", target="192.168.1.1", count=3)
        self.assertTrue(ok["ok"], ok)
        self.assertIn("Test-Connection -ComputerName 192.168.1.1 -Count 3",
                      fake.writes[0][2]["ScriptPrompts"][0]["Value"])

    def test_reboot_builds_shutdown_command(self):
        from execution.skills import kaseya_reboot_machine as rb
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = rb.run(_ctx(fake), machine="11", delay_seconds=120)
        self.assertTrue(r["ok"], r)
        self.assertIn("shutdown /r /f /t 120", fake.writes[0][2]["ScriptPrompts"][0]["Value"])
        self.assertEqual(r["rebooting_in_seconds"], 120)

    def test_restart_service_rejects_metachars(self):
        from execution.skills import kaseya_restart_service as rs
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        self.assertFalse(rs.run(_ctx(fake), machine="11", service="Spooler && evil")["ok"])
        self.assertEqual(fake.writes, [])


class KaseyaADTools(unittest.TestCase):
    """D-72 — AD tools build injection-safe PowerShell, ride the command engine, gated + grouped."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditStore(Path(self.tmp.name) / "ad.db")
        self.reg = Registry()

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def _cmd(self, fake):
        return fake.writes[0][2]["ScriptPrompts"][0]["Value"]

    def test_family_grouped_and_gated(self):
        ad = [t for t in self.reg.all() if t.group == "kaseya_ad"]
        names = {t.name for t in ad}
        for n in ("kaseya_ad_get_user", "kaseya_ad_create_user", "kaseya_ad_reset_password",
                  "kaseya_ad_unlock_account", "kaseya_ad_enable_account",
                  "kaseya_ad_add_group_member", "kaseya_ad_remove_group_member"):
            self.assertIn(n, names)
        for t in ad:
            self.assertEqual(t.source, "kaseya")
            self.assertEqual(t.category, "write")
            self.assertTrue(t.requires_approval)
            self.assertFalse(t.enabled_by_default)
        from execution.core.tool_groups import GROUP_INFO
        self.assertIn("active directory", GROUP_INFO["kaseya_ad"]["title"].lower())

    def test_create_user_builds_newaduser_and_generates_password(self):
        from execution.skills import kaseya_ad_create_user as cu
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = cu.run(_ctx(fake), server="dc-01", first_name="Tommy", last_name="Brown",
                   username="tbrown", email="t@acme.com")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("New-ADUser", cmd)
        self.assertIn("-SamAccountName 'tbrown'", cmd)
        self.assertIn("-Name 'Tommy Brown'", cmd)
        self.assertIn("-EmailAddress 't@acme.com'", cmd)
        self.assertIn("-ChangePasswordAtLogon $true", cmd)
        self.assertTrue(r["initial_password"])           # generated, surfaced once
        self.assertGreaterEqual(len(r["initial_password"]), 16)

    def test_create_user_rejects_bad_email(self):
        from execution.skills import kaseya_ad_create_user as cu
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = cu.run(_ctx(fake), server="dc-01", first_name="A", last_name="B",
                   username="ab", email="not-an-email")
        self.assertFalse(r["ok"])
        self.assertEqual(fake.writes, [])

    def test_ad_inputs_are_injection_safe(self):
        # a malicious username can't break out of the single-quoted PS string
        from execution.skills import kaseya_ad_unlock_account as ua
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = ua.run(_ctx(fake), server="dc-01", user="bob'; Remove-Item C:\\ -Recurse #")
        self.assertTrue(r["ok"], r)                       # runs, but neutralized
        cmd = self._cmd(fake)
        self.assertIn("''", cmd)                          # the quote was doubled, not closed
        self.assertNotIn("'; Remove-Item", cmd.replace("''", "X"))  # no live break-out

    def test_enable_disable_picks_cmdlet(self):
        from execution.skills import kaseya_ad_enable_account as ea
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        ea.run(_ctx(fake), server="dc-01", user="bob", enabled=False)
        self.assertIn("Disable-ADAccount -Identity 'bob'", self._cmd(fake))
        fake2 = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        ea.run(_ctx(fake2), server="dc-01", user="bob", enabled=True)
        self.assertIn("Enable-ADAccount -Identity 'bob'", fake2.writes[0][2]["ScriptPrompts"][0]["Value"])

    def test_group_member_add_and_remove(self):
        from execution.skills import (kaseya_ad_add_group_member as ag,
                                      kaseya_ad_remove_group_member as rg)
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        ag.run(_ctx(fake), server="dc-01", group="VPN Users", user="bob")
        self.assertIn("Add-ADGroupMember -Identity 'VPN Users' -Members 'bob'", self._cmd(fake))
        fake2 = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        rg.run(_ctx(fake2), server="dc-01", group="VPN Users", user="bob")
        self.assertIn("Remove-ADGroupMember -Identity 'VPN Users' -Members 'bob' -Confirm:$false",
                      fake2.writes[0][2]["ScriptPrompts"][0]["Value"])

    def test_reset_password_must_change_default(self):
        from execution.skills import kaseya_ad_reset_password as rp
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = rp.run(_ctx(fake), server="dc-01", user="bob")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("Set-ADAccountPassword -Identity 'bob' -Reset", cmd)
        self.assertIn("-ChangePasswordAtLogon $true", cmd)
        self.assertTrue(r["new_password"])               # generated

    def test_ad_tool_errors_when_engine_procedure_missing(self):
        from execution.skills import kaseya_ad_get_user as gu
        fake = FakeKaseya({"/automation/agentprocs": _env([])})  # no run-command procedure
        r = gu.run(_ctx(fake), server="dc-01", user="bob")
        self.assertFalse(r["ok"])
        self.assertIn("KASEYA_RUN_COMMAND_PROCEDURE", r["error"])

    def test_get_user_batches_into_one_job(self):
        # D-110: a list of users becomes ONE PowerShell job (one approval, one output) that
        # iterates the array — not N separate submissions.
        from execution.skills import kaseya_ad_get_user as gu
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = gu.run(_ctx(fake), server="dc-01", users=["bob", "alice"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(fake.writes), 1)             # ONE job for the whole list
        cmd = self._cmd(fake)
        self.assertIn("@('bob', 'alice')", cmd)           # array of quoted identities
        self.assertIn("ForEach-Object", cmd)
        self.assertIn("Get-ADUser -Identity $u", cmd)     # iterates the captured item
        self.assertEqual(r["looking_up"], ["bob", "alice"])

    def test_create_user_sets_full_profile(self):
        from execution.skills import kaseya_ad_create_user as cu
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = cu.run(_ctx(fake), server="dc-01", first_name="Tommy", last_name="Brown",
                   username="tbrown", title="Tech", department="IT", office="HQ",
                   office_phone="555-1212", manager="jsmith", country="US")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("-Title 'Tech'", cmd)
        self.assertIn("-Department 'IT'", cmd)
        self.assertIn("-OfficePhone '555-1212'", cmd)
        self.assertIn("-Manager 'jsmith'", cmd)
        self.assertIn("-Country 'US'", cmd)

    def test_create_user_country_must_be_two_letters(self):
        from execution.skills import kaseya_ad_create_user as cu
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = cu.run(_ctx(fake), server="dc-01", first_name="A", last_name="B",
                   username="ab", country="USA")
        self.assertFalse(r["ok"])
        self.assertIn("2-letter", r["error"])

    def test_set_user_proxyaddresses_add(self):
        from execution.skills import kaseya_ad_set_user as su
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = su.run(_ctx(fake), server="dc-01", user="tbrown",
                   add_attributes={"proxyAddresses": "smtp:tom.brown@acme.com"})
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("Set-ADUser -Identity 'tbrown'", cmd)
        self.assertIn("-Add @{ 'proxyAddresses'='smtp:tom.brown@acme.com' }", cmd)

    def test_set_user_multivalue_and_replace_and_clear(self):
        from execution.skills import kaseya_ad_set_user as su
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = su.run(_ctx(fake), server="dc-01", user="tbrown", title="Senior Tech",
                   set_attributes={"extensionAttribute1": "VIP"},
                   add_attributes={"proxyAddresses": ["smtp:a@x.com", "smtp:b@x.com"]},
                   clear_attributes=["facsimileTelephoneNumber"])
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("-Title 'Senior Tech'", cmd)
        self.assertIn("-Replace @{ 'extensionAttribute1'='VIP' }", cmd)
        self.assertIn("-Add @{ 'proxyAddresses'=@('smtp:a@x.com','smtp:b@x.com') }", cmd)
        self.assertIn("-Clear facsimileTelephoneNumber", cmd)

    def test_set_user_rejects_bad_attribute_name(self):
        from execution.skills import kaseya_ad_set_user as su
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = su.run(_ctx(fake), server="dc-01", user="tbrown",
                   set_attributes={"evil'; Remove-Item C:\\": "x"})
        self.assertFalse(r["ok"])
        self.assertIn("invalid attribute name", r["error"])
        self.assertEqual(fake.writes, [])

    def test_set_user_needs_a_change(self):
        from execution.skills import kaseya_ad_set_user as su
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = su.run(_ctx(fake), server="dc-01", user="tbrown")
        self.assertFalse(r["ok"])
        self.assertIn("at least one", r["error"])

    def test_entra_delta_sync(self):
        from execution.skills import kaseya_entra_delta_sync as ds
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = ds.run(_ctx(fake), server="dc-01")     # the AAD Connect server's machine
        self.assertTrue(r["ok"], r)
        self.assertIn("Start-ADSyncSyncCycle -PolicyType Delta", self._cmd(fake))
        ds.run(_ctx(fake), server="dc-01", type="initial")
        self.assertIn("Start-ADSyncSyncCycle -PolicyType Initial",
                      fake.writes[1][2]["ScriptPrompts"][0]["Value"])


class KaseyaGenericBuildingBlocks(unittest.TestCase):
    """D-74 — generic (any-client) primitives: create group, provision folders, set NTFS perms."""

    def _cmd(self, fake):
        return fake.writes[0][2]["ScriptPrompts"][0]["Value"]

    def test_create_group_builds_newadgroup_with_scope_category(self):
        from execution.skills import kaseya_ad_create_group as cg
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = cg.run(_ctx(fake), server="dc-01", name="VPN Users", scope="universal",
                   category="distribution", ou="OU=Groups,DC=acme,DC=local", description="vpn")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("New-ADGroup -Name 'VPN Users' -SamAccountName 'VPN Users'", cmd)
        self.assertIn("-GroupScope Universal -GroupCategory Distribution", cmd)
        self.assertIn("-Path 'OU=Groups,DC=acme,DC=local'", cmd)
        self.assertIn("-Description 'vpn'", cmd)
        self.assertIn("if ($exists)", cmd)                 # idempotent skip-if-exists

    def test_create_group_rejects_bad_scope(self):
        from execution.skills import kaseya_ad_create_group as cg
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = cg.run(_ctx(fake), server="dc-01", name="X", scope="planet")
        self.assertFalse(r["ok"])
        self.assertEqual(fake.writes, [])

    def test_provision_folders_clone_vs_plain(self):
        from execution.skills import kaseya_fs_provision_folders as pf
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = pf.run(_ctx(fake), server="dc-01", target=r"\\fs\Share\New",
                   sample_dir=r"\\fs\Share\Template")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("robocopy $sample $target /E /XF *", cmd)
        self.assertIn("$target = '\\\\fs\\Share\\New'", cmd)
        fake2 = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        pf.run(_ctx(fake2), server="dc-01", target=r"D:\Data\Folder")   # no sample → New-Item
        cmd2 = fake2.writes[0][2]["ScriptPrompts"][0]["Value"]
        self.assertIn("New-Item -ItemType Directory", cmd2)
        self.assertNotIn("robocopy", cmd2)

    def test_provision_folders_rejects_bad_path(self):
        from execution.skills import kaseya_fs_provision_folders as pf
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = pf.run(_ctx(fake), server="dc-01", target=r"\\fs\Bad?Name")
        self.assertFalse(r["ok"])
        self.assertEqual(fake.writes, [])

    def test_set_permissions_friendly_and_raw_and_remove(self):
        from execution.skills import kaseya_fs_set_permissions as sp
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = sp.run(_ctx(fake), server="dc-01", path=r"\\fs\Share\New",
                   grant={"ACME\\Staff": "modify", "SYSTEM": "(OI)(CI)F"},
                   remove=["ACME\\Temp"], disable_inheritance=True)
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("icacls $path /inheritance:r", cmd)
        self.assertIn(r"'ACME\Staff:(OI)(CI)M'", cmd)        # friendly word → icacls code
        self.assertIn("'SYSTEM:(OI)(CI)F'", cmd)             # raw spec passed through
        self.assertIn(r"/remove 'ACME\Temp'", cmd)

    def test_set_permissions_validation(self):
        from execution.skills import kaseya_fs_set_permissions as sp
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        # injection / bad principal rejected
        self.assertFalse(sp.run(_ctx(fake), server="dc-01", path=r"\\fs\x",
                                grant={"bad'; rm": "full"})["ok"])
        # bad access rejected
        self.assertFalse(sp.run(_ctx(fake), server="dc-01", path=r"\\fs\x",
                                grant={"ACME\\Staff": "godmode"})["ok"])
        # nothing to do rejected
        self.assertFalse(sp.run(_ctx(fake), server="dc-01", path=r"\\fs\x")["ok"])
        self.assertEqual(fake.writes, [])

    def test_get_permissions_reports_acl(self):
        from execution.skills import kaseya_fs_get_permissions as gp
        fake = FakeKaseya({"/automation/agentprocs": _CMD_PROCS})
        r = gp.run(_ctx(fake), server="dc-01", path=r"\\fs\Share\New")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("Get-Acl -LiteralPath $path", cmd)
        self.assertIn("$path = '\\\\fs\\Share\\New'", cmd)
        self.assertIn("$acl.Access", cmd)
        self.assertNotIn("/grant", cmd)                    # read-only: no mutation
        r2 = gp.run(_ctx(fake), server="dc-01", path=r"\\fs\Bad*Path")
        self.assertFalse(r2["ok"])

    def test_fs_group_registered(self):
        from execution.core.tool_groups import GROUP_INFO
        self.assertIn("kaseya_fs", GROUP_INFO)
        for key in ("title", "setup", "how_to", "icon"):
            self.assertTrue(GROUP_INFO["kaseya_fs"][key])


class KaseyaDHCP(unittest.TestCase):
    """D-76 — DHCP suite: scopes/stats/leases/reservations + adjust, via the command engine."""

    def _cmd(self, fake):
        return fake.writes[0][2]["ScriptPrompts"][0]["Value"]

    def _fake(self):
        return FakeKaseya({"/automation/agentprocs": _CMD_PROCS})

    def test_family_grouped_and_gated(self):
        ad = [t for t in self.reg.all() if t.group == "kaseya_dhcp"]
        names = {t.name for t in ad}
        for n in ("kaseya_dhcp_list_scopes", "kaseya_dhcp_scope_stats", "kaseya_dhcp_list_leases",
                  "kaseya_dhcp_list_reservations", "kaseya_dhcp_set_scope",
                  "kaseya_dhcp_add_reservation", "kaseya_dhcp_remove_reservation",
                  "kaseya_dhcp_add_exclusion", "kaseya_dhcp_remove_exclusion"):
            self.assertIn(n, names)
        for t in ad:
            self.assertEqual(t.source, "kaseya")
            self.assertEqual(t.category, "write")           # all ride the command engine
            self.assertTrue(t.requires_approval)
            self.assertFalse(t.enabled_by_default)
        from execution.core.tool_groups import GROUP_INFO
        self.assertIn("dhcp", GROUP_INFO["kaseya_dhcp"]["title"].lower())

    def setUp(self):
        self.reg = Registry()

    def test_scope_stats_and_bad_scope(self):
        from execution.skills import kaseya_dhcp_scope_stats as ss
        fake = self._fake()
        r = ss.run(_ctx(fake), server="dc-01", scope_id="192.168.1.0")
        self.assertTrue(r["ok"], r)
        self.assertIn("Get-DhcpServerv4ScopeStatistics -ScopeId '192.168.1.0'", self._cmd(fake))
        bad = ss.run(_ctx(self._fake()), server="dc-01", scope_id="not-an-ip")
        self.assertFalse(bad["ok"])

    def test_set_scope_builds_changes_and_requires_one(self):
        from execution.skills import kaseya_dhcp_set_scope as st
        fake = self._fake()
        r = st.run(_ctx(fake), server="dc-01", scope_id="10.0.0.0", name="Office",
                   state="inactive", lease_duration_days=8,
                   start_range="10.0.0.50", end_range="10.0.0.200")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("Set-DhcpServerv4Scope -ScopeId '10.0.0.0'", cmd)
        self.assertIn("-Name 'Office'", cmd)
        self.assertIn("-State Inactive", cmd)
        self.assertIn("-LeaseDuration (New-TimeSpan -Days 8)", cmd)
        self.assertIn("-StartRange '10.0.0.50' -EndRange '10.0.0.200'", cmd)
        # nothing-to-change rejected
        none = st.run(_ctx(self._fake()), server="dc-01", scope_id="10.0.0.0")
        self.assertFalse(none["ok"])
        # half a range rejected
        half = st.run(_ctx(self._fake()), server="dc-01", scope_id="10.0.0.0", start_range="10.0.0.5")
        self.assertFalse(half["ok"])

    def test_add_reservation_normalizes_mac(self):
        from execution.skills import kaseya_dhcp_add_reservation as ar
        fake = self._fake()
        r = ar.run(_ctx(fake), server="dc-01", scope_id="192.168.1.0", ip="192.168.1.25",
                   mac="AABB.CCDD.EEFF", name="Printer")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("Add-DhcpServerv4Reservation -ScopeId '192.168.1.0'", cmd)
        self.assertIn("-IPAddress '192.168.1.25'", cmd)
        self.assertIn("-ClientId 'aa-bb-cc-dd-ee-ff'", cmd)       # normalized
        self.assertEqual(r["mac"], "aa-bb-cc-dd-ee-ff")
        bad = ar.run(_ctx(self._fake()), server="dc-01", scope_id="192.168.1.0",
                     ip="192.168.1.25", mac="ZZZZ")
        self.assertFalse(bad["ok"])

    def test_remove_reservation_and_exclusions(self):
        from execution.skills import (kaseya_dhcp_remove_reservation as rr,
                                      kaseya_dhcp_add_exclusion as ae,
                                      kaseya_dhcp_remove_exclusion as re_)
        fake = self._fake()
        rr.run(_ctx(fake), server="dc-01", scope_id="192.168.1.0", ip="192.168.1.25")
        self.assertIn("Remove-DhcpServerv4Reservation -ScopeId '192.168.1.0' "
                      "-IPAddress '192.168.1.25' -Confirm:$false", self._cmd(fake))
        f2 = self._fake()
        ae.run(_ctx(f2), server="dc-01", scope_id="192.168.1.0",
               start_range="192.168.1.2", end_range="192.168.1.10")
        self.assertIn("Add-DhcpServerv4ExclusionRange -ScopeId '192.168.1.0' "
                      "-StartRange '192.168.1.2' -EndRange '192.168.1.10'",
                      f2.writes[0][2]["ScriptPrompts"][0]["Value"])
        f3 = self._fake()
        re_.run(_ctx(f3), server="dc-01", scope_id="192.168.1.0",
                start_range="192.168.1.2", end_range="192.168.1.10")
        self.assertIn("Remove-DhcpServerv4ExclusionRange",
                      f3.writes[0][2]["ScriptPrompts"][0]["Value"])

    def test_mac_normalizer(self):
        from execution.skills import _kaseya_common as k
        for raw in ("aabbccddeeff", "AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "AABB.CCDD.EEFF"):
            self.assertEqual(k.clean_mac(raw), "aa-bb-cc-dd-ee-ff")
        self.assertIsNone(k.clean_mac("aabbccddee"))     # too short
        self.assertIsNone(k.clean_mac("gg-hh-ii-jj-kk-ll"))


class KaseyaGpoDnsEvents(unittest.TestCase):
    """D-77/78/79 — Group Policy, DNS, and Event Viewer tools on the command engine."""

    def setUp(self):
        self.reg = Registry()

    def _fake(self):
        return FakeKaseya({"/automation/agentprocs": _CMD_PROCS})

    def _cmd(self, fake):
        return fake.writes[0][2]["ScriptPrompts"][0]["Value"]

    def test_groups_registered_and_gated(self):
        from execution.core.tool_groups import GROUP_INFO
        for g, want in (("kaseya_gpo", "group policy"), ("kaseya_dns", "dns"),
                        ("kaseya_events", "event")):
            self.assertIn(g, GROUP_INFO)
            self.assertIn(want, GROUP_INFO[g]["title"].lower())
            tools = [t for t in self.reg.all() if t.group == g]
            self.assertTrue(tools)
            for t in tools:
                self.assertEqual(t.source, "kaseya")
                self.assertEqual(t.category, "write")
                self.assertTrue(t.requires_approval)
                self.assertFalse(t.enabled_by_default)

    def test_gpo_link_unlink_and_update(self):
        from execution.skills import (kaseya_gpo_link as gl, kaseya_gpo_unlink as gu,
                                      kaseya_gpo_update as gp)
        f = self._fake()
        gl.run(_ctx(f), server="dc-01", name="Default Policy",
               target="OU=Staff,DC=acme,DC=local", enforced=True)
        self.assertIn("New-GPLink -Name 'Default Policy' -Target 'OU=Staff,DC=acme,DC=local' "
                      "-LinkEnabled Yes -Enforced Yes", self._cmd(f))
        f2 = self._fake()
        gu.run(_ctx(f2), server="dc-01", name="Default Policy", target="OU=Staff,DC=acme,DC=local")
        self.assertIn("Remove-GPLink -Name 'Default Policy'", self._cmd(f2))
        f3 = self._fake()
        gp.run(_ctx(f3), server="dc-01")
        self.assertIn("gpupdate /target:computer /force", self._cmd(f3))

    def test_gpo_create_and_set_remove_registry(self):
        from execution.skills import (kaseya_gpo_create as gc, kaseya_gpo_set_registry as gs,
                                      kaseya_gpo_remove_registry as gr)
        f = self._fake()
        gc.run(_ctx(f), server="dc-01", name="Win Update Policy", comment="patches")
        self.assertIn("New-GPO -Name 'Win Update Policy' -Comment 'patches'", self._cmd(f))
        # set a DWORD admin-template value
        f2 = self._fake()
        r = gs.run(_ctx(f2), server="dc-01", name="Win Update Policy",
                   key=r"HKLM\Software\Policies\Microsoft\Windows\WindowsUpdate\AU",
                   value_name="NoAutoUpdate", type="dword", value="1")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(f2)
        self.assertIn("Set-GPRegistryValue -Name 'Win Update Policy'", cmd)
        self.assertIn(r"-Key 'HKLM\Software\Policies\Microsoft\Windows\WindowsUpdate\AU'", cmd)
        self.assertIn("-ValueName 'NoAutoUpdate' -Type DWord -Value 1", cmd)
        # multistring uses @(...)
        f3 = self._fake()
        gs.run(_ctx(f3), server="dc-01", name="P", key=r"HKCU\Software\Policies\X",
               value_name="List", type="multistring", values=["a", "b"])
        self.assertIn("-Type MultiString -Value @('a','b')", self._cmd(f3))
        # bad key (not under HKLM/HKCU) rejected
        bad = gs.run(_ctx(self._fake()), server="dc-01", name="P",
                     key=r"SOFTWARE\X", value_name="v", type="dword", value="1")
        self.assertFalse(bad["ok"])
        # dword needs a number
        nan = gs.run(_ctx(self._fake()), server="dc-01", name="P",
                     key=r"HKLM\Software\X", value_name="v", type="dword", value="nope")
        self.assertFalse(nan["ok"])
        # remove
        f4 = self._fake()
        gr.run(_ctx(f4), server="dc-01", name="Win Update Policy",
               key=r"HKLM\Software\Policies\Microsoft\Windows\WindowsUpdate\AU",
               value_name="NoAutoUpdate")
        self.assertIn("Remove-GPRegistryValue -Name 'Win Update Policy'", self._cmd(f4))
        self.assertIn("-ValueName 'NoAutoUpdate'", self._cmd(f4))

    def test_dns_add_record_types(self):
        from execution.skills import kaseya_dns_add_record as ar
        f = self._fake()
        ar.run(_ctx(f), server="dns-01", zone="acme.local", type="A", name="www", data="10.0.0.5")
        self.assertIn("Add-DnsServerResourceRecordA -ZoneName 'acme.local' -Name 'www' "
                      "-IPv4Address '10.0.0.5'", self._cmd(f))
        f2 = self._fake()
        ar.run(_ctx(f2), server="dns-01", zone="acme.local", type="MX", name="@",
               data="mail.acme.local", priority=10)
        self.assertIn("-MailExchange 'mail.acme.local' -Preference 10", self._cmd(f2))
        # A record with a non-IP is rejected
        bad = ar.run(_ctx(self._fake()), server="dns-01", zone="acme.local", type="A",
                     name="www", data="not-an-ip")
        self.assertFalse(bad["ok"])
        # MX without priority is rejected
        nomx = ar.run(_ctx(self._fake()), server="dns-01", zone="acme.local", type="MX",
                      name="@", data="mail.acme.local")
        self.assertFalse(nomx["ok"])

    def test_dns_remove_and_resolve_and_clear(self):
        from execution.skills import (kaseya_dns_remove_record as rr, kaseya_dns_resolve as rs,
                                      kaseya_dns_clear_cache as cc)
        f = self._fake()
        rr.run(_ctx(f), server="dns-01", zone="acme.local", type="A", name="www", data="10.0.0.5")
        self.assertIn("Remove-DnsServerResourceRecord -ZoneName 'acme.local' -Name 'www' "
                      "-RRType A -RecordData '10.0.0.5' -Force", self._cmd(f))
        f2 = self._fake()
        rs.run(_ctx(f2), server="iwr-01", name="google.com", type="A")
        self.assertIn("Resolve-DnsName -Name 'google.com' -Type A", self._cmd(f2))
        f3 = self._fake()
        cc.run(_ctx(f3), server="dns-01")
        self.assertIn("Clear-DnsServerCache", self._cmd(f3))

    def test_dns_zone_validation(self):
        from execution.skills import kaseya_dns_add_record as ar
        r = ar.run(_ctx(self._fake()), server="dns-01", zone="bad zone!", type="A",
                   name="www", data="10.0.0.5")
        self.assertFalse(r["ok"])

    def test_event_query_builds_filter(self):
        from execution.skills import kaseya_event_query as eq
        f = self._fake()
        r = eq.run(_ctx(f), server="iwr-01", log="Application", level="error",
                   since_hours=24, event_id=1000, provider="MyApp", count=25)
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(f)
        self.assertIn("LogName='Application'", cmd)
        self.assertIn("Level=2", cmd)                       # error → 2
        self.assertIn("Id=1000", cmd)
        self.assertIn("ProviderName='MyApp'", cmd)
        self.assertIn("StartTime=(Get-Date).AddHours(-24)", cmd)
        self.assertIn("-MaxEvents 25", cmd)

    def test_event_query_defaults_and_validation(self):
        from execution.skills import kaseya_event_query as eq
        f = self._fake()
        r = eq.run(_ctx(f), server="iwr-01")                # all defaults
        self.assertTrue(r["ok"], r)
        self.assertIn("LogName='System'", self._cmd(f))
        self.assertIn("-MaxEvents 50", self._cmd(f))
        self.assertFalse(eq.run(_ctx(self._fake()), server="iwr-01", level="boom")["ok"])


class KaseyaRegistry(unittest.TestCase):
    """D-80 — machine registry read/edit/delete on the command engine; key-delete is destructive."""

    def setUp(self):
        self.reg = Registry()

    def _fake(self):
        return FakeKaseya({"/automation/agentprocs": _CMD_PROCS})

    def _cmd(self, fake):
        return fake.writes[0][2]["ScriptPrompts"][0]["Value"]

    def test_group_and_categories(self):
        from execution.core.tool_groups import GROUP_INFO
        self.assertIn("kaseya_registry", GROUP_INFO)
        tools = {t.name: t for t in self.reg.all() if t.group == "kaseya_registry"}
        for n in ("kaseya_registry_get", "kaseya_registry_set", "kaseya_registry_delete_value",
                  "kaseya_registry_delete_key"):
            self.assertIn(n, tools)
            self.assertTrue(tools[n].requires_approval)
            self.assertFalse(tools[n].enabled_by_default)
        # the recursive key delete is DESTRUCTIVE (always-approval floor)
        self.assertEqual(tools["kaseya_registry_delete_key"].category, "destructive")
        self.assertEqual(tools["kaseya_registry_set"].category, "write")

    def test_get_value_and_list(self):
        from execution.skills import kaseya_registry_get as rg
        f = self._fake()
        rg.run(_ctx(f), server="iwr-01",
               key=r"HKLM\Software\Microsoft\Windows\CurrentVersion", value_name="ProgramFilesDir")
        cmd = self._cmd(f)
        self.assertIn("Get-ItemPropertyValue -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\\Software"
                      "\\Microsoft\\Windows\\CurrentVersion' -Name 'ProgramFilesDir'", cmd)
        f2 = self._fake()
        rg.run(_ctx(f2), server="iwr-01", key=r"HKCU\Software\X")     # no value → list
        self.assertIn("Get-ChildItem -LiteralPath 'Registry::HKEY_CURRENT_USER\\Software\\X'",
                      self._cmd(f2))

    def test_set_dword_and_multistring_and_create_key(self):
        from execution.skills import kaseya_registry_set as rs
        f = self._fake()
        r = rs.run(_ctx(f), server="iwr-01", key=r"HKLM\Software\Acme",
                   value_name="Enabled", type="dword", value="1")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(f)
        self.assertIn("if (-not (Test-Path -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\\Software"
                      "\\Acme')) { New-Item -Path", cmd)
        self.assertIn("-Name 'Enabled' -PropertyType DWord -Value 1 -Force", cmd)
        f2 = self._fake()
        rs.run(_ctx(f2), server="iwr-01", key=r"HKLM\Software\Acme", value_name="List",
               type="multistring", values=["a", "b"])
        self.assertIn("-PropertyType MultiString -Value @('a','b')", self._cmd(f2))

    def test_set_validation(self):
        from execution.skills import kaseya_registry_set as rs
        # bad hive
        self.assertFalse(rs.run(_ctx(self._fake()), server="iwr-01", key=r"BOGUS\X",
                                value_name="v", type="dword", value="1")["ok"])
        # dword needs a number
        self.assertFalse(rs.run(_ctx(self._fake()), server="iwr-01", key=r"HKLM\Software\X",
                                value_name="v", type="dword", value="abc")["ok"])

    def test_delete_value_and_key_and_hive_guard(self):
        from execution.skills import (kaseya_registry_delete_value as dv,
                                      kaseya_registry_delete_key as dk)
        f = self._fake()
        dv.run(_ctx(f), server="iwr-01", key=r"HKLM\Software\Acme", value_name="Enabled")
        self.assertIn("Remove-ItemProperty -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\\Software"
                      "\\Acme' -Name 'Enabled' -Force", self._cmd(f))
        f2 = self._fake()
        dk.run(_ctx(f2), server="iwr-01", key=r"HKLM\Software\Acme")
        self.assertIn("Remove-Item -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\\Software\\Acme' "
                      "-Recurse -Force", self._cmd(f2))
        # refuse a bare hive root
        bad = dk.run(_ctx(self._fake()), server="iwr-01", key="HKLM")
        self.assertFalse(bad["ok"])
        self.assertIn("hive root", bad["error"])

    def test_registry_injection_safe(self):
        from execution.skills import kaseya_registry_set as rs
        f = self._fake()
        r = rs.run(_ctx(f), server="iwr-01", key=r"HKLM\Software\Acme",
                   value_name="v", type="string", value="x'; Remove-Item C:\\ #")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(f)
        self.assertIn("''", cmd)                              # quote doubled
        self.assertNotIn("'; Remove-Item", cmd.replace("''", "X"))


class KaseyaUnifiField(unittest.TestCase):
    """D-85 — UniFi field tools: scan + SSH set-inform / factory-reset via the command engine."""

    def setUp(self):
        self.reg = Registry()

    def _fake(self):
        return FakeKaseya({"/automation/agentprocs": _CMD_PROCS})

    def _cmd(self, fake):
        return fake.writes[0][2]["ScriptPrompts"][0]["Value"]

    def test_group_and_categories(self):
        tools = {t.name: t for t in self.reg.all() if t.group == "kaseya_unifi"}
        self.assertEqual(tools["kaseya_unifi_factory_reset"].category, "destructive")
        for n in ("kaseya_unifi_scan", "kaseya_unifi_set_inform"):
            self.assertEqual(tools[n].category, "write")
        for t in tools.values():
            self.assertEqual(t.source, "kaseya")
            self.assertTrue(t.requires_approval)
            self.assertFalse(t.enabled_by_default)
        from execution.core.tool_groups import GROUP_INFO
        self.assertIn("unifi", GROUP_INFO["kaseya_unifi"]["title"].lower())

    def test_scan_builds_arp_oui_command(self):
        from execution.skills import kaseya_unifi_scan as sc
        fake = self._fake()
        r = sc.run(_ctx(fake), machine="iwr-01")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("SendPingAsync", cmd)
        self.assertIn("arp -a", cmd)
        self.assertIn("'245a4c'", cmd)                    # a Ubiquiti OUI got injected

    def test_set_inform_builds_url_and_validates(self):
        from execution.skills import kaseya_unifi_set_inform as si
        fake = self._fake()
        r = si.run(_ctx(fake), machine="iwr-01", device_ip="192.168.1.50",
                   controller="192.168.1.2", ssh_user="ubnt", ssh_pass="ubnt")
        self.assertTrue(r["ok"], r)
        cmd = self._cmd(fake)
        self.assertIn("set-inform http://192.168.1.2:8080/inform", cmd)
        self.assertIn("'ubnt@192.168.1.50'", cmd)
        self.assertIn("-ssh -pw", cmd)
        # bad device ip rejected
        self.assertFalse(si.run(_ctx(self._fake()), machine="iwr-01", device_ip="nope",
                                controller="192.168.1.2")["ok"])

    def test_set_inform_injection_safe(self):
        from execution.skills import kaseya_unifi_set_inform as si
        fake = self._fake()
        si.run(_ctx(fake), machine="iwr-01", device_ip="192.168.1.50",
               controller="10.0.0.2", ssh_pass="x'; del C:\\ #")
        cmd = self._cmd(fake)
        self.assertIn("''", cmd)
        self.assertNotIn("'; del", cmd.replace("''", "X"))

    def test_factory_reset_is_destructive_set_default(self):
        from execution.skills import kaseya_unifi_factory_reset as fr
        fake = self._fake()
        r = fr.run(_ctx(fake), machine="iwr-01", device_ip="192.168.1.50")
        self.assertTrue(r["ok"], r)
        self.assertIn("set-default", self._cmd(fake))


class KaseyaTroubleshooting(unittest.TestCase):
    """D-81 — Diagnostics / Remediation / Network / Windows Update packs on the command engine."""

    def setUp(self):
        self.reg = Registry()

    def _fake(self):
        return FakeKaseya({"/automation/agentprocs": _CMD_PROCS})

    def _cmd(self, fake):
        return fake.writes[0][2]["ScriptPrompts"][0]["Value"]

    def test_packs_registered_and_gated(self):
        from execution.core.tool_groups import GROUP_INFO
        expect = {
            "kaseya_diag": {"kaseya_diag_processes", "kaseya_diag_services", "kaseya_diag_system",
                            "kaseya_diag_network", "kaseya_diag_disk", "kaseya_diag_certs",
                            "kaseya_diag_scheduled_tasks", "kaseya_diag_sessions"},
            "kaseya_net": {"kaseya_net_port_test", "kaseya_net_traceroute", "kaseya_net_adapter"},
            "kaseya_update": {"kaseya_update_check", "kaseya_update_install"},
        }
        for g, names in expect.items():
            self.assertIn(g, GROUP_INFO)
            have = {t.name for t in self.reg.all() if t.group == g}
            self.assertTrue(names.issubset(have), (g, names - have))
            for t in self.reg.all():
                if t.group == g:
                    self.assertEqual(t.category, "write")
                    self.assertTrue(t.requires_approval)
                    self.assertFalse(t.enabled_by_default)
        # remediation tools joined the existing Command Toolkit group
        cmd = {t.name for t in self.reg.all() if t.group == "kaseya_command"}
        for n in ("kaseya_kill_process", "kaseya_service_control", "kaseya_clear_print_spooler",
                  "kaseya_disk_cleanup", "kaseya_repair_system", "kaseya_reset_network",
                  "kaseya_renew_ip", "kaseya_uninstall_software"):
            self.assertIn(n, cmd)

    def test_diag_processes_and_system(self):
        from execution.skills import kaseya_diag_processes as dp, kaseya_diag_system as ds
        f = self._fake()
        dp.run(_ctx(f), server="iwr-01")
        self.assertIn("Get-Process", self._cmd(f))
        f2 = self._fake()
        ds.run(_ctx(f2), server="iwr-01")
        self.assertIn("Win32_OperatingSystem", self._cmd(f2))
        self.assertIn("Reboot pending", self._cmd(f2))

    def test_kill_process_by_name_and_pid(self):
        from execution.skills import kaseya_kill_process as kp
        f = self._fake()
        kp.run(_ctx(f), server="iwr-01", process="chrome.exe")
        self.assertIn("Stop-Process -Name 'chrome' -Force", self._cmd(f))   # .exe stripped
        f2 = self._fake()
        kp.run(_ctx(f2), server="iwr-01", pid=4321)
        self.assertIn("Stop-Process -Id 4321 -Force", self._cmd(f2))
        # neither given
        self.assertFalse(kp.run(_ctx(self._fake()), server="iwr-01")["ok"])
        # injection in name rejected
        self.assertFalse(kp.run(_ctx(self._fake()), server="iwr-01",
                                process="x'; Remove-Item C:\\")["ok"])

    def test_service_control_combinations(self):
        from execution.skills import kaseya_service_control as sc
        f = self._fake()
        sc.run(_ctx(f), server="iwr-01", service="Spooler", action="restart",
               start_type="automatic")
        cmd = self._cmd(f)
        self.assertIn("Set-Service -Name 'Spooler' -StartupType Automatic", cmd)
        self.assertIn("Restart-Service -Name 'Spooler' -Force", cmd)
        # nothing to do
        self.assertFalse(sc.run(_ctx(self._fake()), server="iwr-01", service="Spooler")["ok"])

    def test_net_port_test_and_traceroute_validation(self):
        from execution.skills import kaseya_net_port_test as pt, kaseya_net_traceroute as tr
        f = self._fake()
        pt.run(_ctx(f), server="iwr-01", host="mail.acme.com", port=443)
        self.assertIn("Test-NetConnection -ComputerName 'mail.acme.com' -Port 443", self._cmd(f))
        self.assertFalse(pt.run(_ctx(self._fake()), server="iwr-01", host="x", port=99999)["ok"])
        f2 = self._fake()
        tr.run(_ctx(f2), server="iwr-01", host="8.8.8.8")
        self.assertIn("tracert -d -h 20 '8.8.8.8'", self._cmd(f2))
        self.assertFalse(tr.run(_ctx(self._fake()), server="iwr-01",
                                host="bad host!")["ok"])

    def test_net_adapter_list_vs_control(self):
        from execution.skills import kaseya_net_adapter as na
        f = self._fake()
        na.run(_ctx(f), server="iwr-01", action="list")
        self.assertIn("Get-NetAdapter", self._cmd(f))
        f2 = self._fake()
        na.run(_ctx(f2), server="iwr-01", action="disable", name="Ethernet 2")
        self.assertIn("Disable-NetAdapter -Name 'Ethernet 2' -Confirm:$false", self._cmd(f2))
        # disable without a name is rejected
        self.assertFalse(na.run(_ctx(self._fake()), server="iwr-01", action="disable")["ok"])

    def test_update_tools_use_com_api(self):
        from execution.skills import kaseya_update_check as uc, kaseya_update_install as ui
        f = self._fake()
        uc.run(_ctx(f), server="iwr-01")
        self.assertIn("Microsoft.Update.Session", self._cmd(f))
        f2 = self._fake()
        ui.run(_ctx(f2), server="iwr-01")
        self.assertIn("CreateUpdateInstaller", self._cmd(f2))


if __name__ == "__main__":
    unittest.main()
