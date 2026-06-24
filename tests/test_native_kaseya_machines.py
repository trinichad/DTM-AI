"""Native bulk `machines` param on the Kaseya per-machine skills (D-110-style fan-out).

Each tool now takes `machines` (a list) alongside `machine`, so the agent acts on MANY boxes in
ONE call instead of looping. We assert: a list yields one result row per machine (each carrying
its own machine identifier), machines_done is correct, and the single-machine path is unchanged.
Reuses the fakes/helpers from the existing Kaseya suite (no edits there)."""
import unittest

from tests.test_kaseya import FakeKaseya, _ctx, _env, _CMD_PROCS, _AGENTS


def _two_machine_ctx():
    """A fake that resolves both iwr-01 (id 11) and iwr-02 (id 12), with canned audit reads
    keyed on each AgentId so the per-machine read tools succeed for both."""
    routes = {}
    for aid in (11, 12):
        routes[f"/assetmgmt/agents/{aid}"] = _env(
            {"AgentId": aid, "AgentName": f"iwr-0{aid - 10}.acme.local", "Online": 1,
             "LastRebootTime": "2026-06-10T03:00:00", "IPAddress": f"10.0.0.{aid}"})
        routes[f"/assetmgmt/audit/{aid}/hardware/diskvolumes"] = _env(
            [{"DriveLetter": "C:", "TotalBytes": 1000, "FreeBytes": 250}])
        routes[f"/assetmgmt/audit/{aid}/summary"] = _env({"AuditTime": "2026-06-10"})
    return FakeKaseya(routes)


class NativeMachinesReads(unittest.TestCase):
    def test_machine_health_batch_and_single(self):
        from execution.skills import kaseya_machine_health as mh
        fake = _two_machine_ctx()
        r = mh.run(_ctx(fake), machines=["iwr-01", "iwr-02"])
        self.assertEqual(r["machines_done"], 2)
        self.assertEqual(len(r["results"]), 2)
        self.assertTrue(r["ok"])
        self.assertEqual(r["ok_count"], 2)
        # each row is attributable to its machine
        self.assertEqual(r["results"][0]["machine"], "iwr-01.acme.local")
        self.assertEqual(r["results"][1]["machine"], "iwr-02.acme.local")
        # single-machine path unchanged: a bare dict, not a {results:[...]} envelope
        one = mh.run(_ctx(fake), machine="iwr-01")
        self.assertTrue(one["ok"])
        self.assertNotIn("results", one)
        self.assertEqual(one["agent_id"], 11)

    def test_batch_carries_machine_on_failure(self):
        from execution.skills import kaseya_machine_health as mh
        fake = _two_machine_ctx()
        r = mh.run(_ctx(fake), machines=["iwr-01", "nope-99"])
        self.assertEqual(r["machines_done"], 2)
        self.assertEqual(r["ok_count"], 1)
        self.assertTrue(r["ok"])                       # any() — one succeeded
        bad = r["results"][1]
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["machine"], "nope-99")    # error row still attributable
        self.assertIn("no Kaseya agent", bad["error"])

    def test_disk_volumes_batch(self):
        from execution.skills import kaseya_disk_volumes as dv
        fake = _two_machine_ctx()
        r = dv.run(_ctx(fake), machines=["11", "12"])
        self.assertEqual(r["machines_done"], 2)
        for row in r["results"]:
            self.assertTrue(row["ok"], row)
            self.assertEqual(row["volumes"][0]["percent_free"], 25.0)
        # single path
        one = dv.run(_ctx(fake), machine="11")
        self.assertTrue(one["ok"])
        self.assertNotIn("results", one)

    def test_audit_summary_batch(self):
        from execution.skills import kaseya_audit_summary as au
        fake = _two_machine_ctx()
        r = au.run(_ctx(fake), machines=["iwr-01", "iwr-02"])
        self.assertEqual(r["machines_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual({row["machine"] for row in r["results"]},
                         {"iwr-01.acme.local", "iwr-02.acme.local"})


class NativeMachinesWrites(unittest.TestCase):
    def _fake(self):
        return FakeKaseya({"/automation/agentprocs": _CMD_PROCS})

    def test_reboot_batch_schedules_each_machine(self):
        from execution.skills import kaseya_reboot_machine as rb
        fake = self._fake()
        r = rb.run(_ctx(fake), machines=["iwr-01", "iwr-02"], delay_seconds=120)
        self.assertEqual(r["machines_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual(len(fake.writes), 2)         # one command job per machine
        for row in r["results"]:
            self.assertTrue(row["ok"], row)
            self.assertEqual(row["rebooting_in_seconds"], 120)
            self.assertIn("machine", row)
        # commands targeted the two resolved AgentIds
        scheduled_paths = {w[1] for w in fake.writes}
        self.assertEqual(scheduled_paths, {"/automation/agentprocs/11/500/schedule",
                                           "/automation/agentprocs/12/500/schedule"})
        # single path unchanged
        f2 = self._fake()
        one = rb.run(_ctx(f2), machine="11", delay_seconds=60)
        self.assertTrue(one["ok"])
        self.assertNotIn("results", one)
        self.assertEqual(one["rebooting_in_seconds"], 60)

    def test_run_command_batch_threads_command(self):
        from execution.skills import kaseya_run_command as rc
        fake = self._fake()
        r = rc.run(_ctx(fake), machines=["iwr-01", "iwr-02"], command="ipconfig /all")
        self.assertEqual(r["machines_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        self.assertEqual(len(fake.writes), 2)
        for w in fake.writes:
            self.assertEqual(w[2]["ScriptPrompts"][0]["Value"], "ipconfig /all")
        for row in r["results"]:
            self.assertIn("machine", row)
        # single path
        f2 = self._fake()
        one = rc.run(_ctx(f2), machine="iwr-01", command="whoami")
        self.assertTrue(one["ok"])
        self.assertNotIn("results", one)
        self.assertEqual(f2.writes[0][2]["ScriptPrompts"][0]["Value"], "whoami")

    def test_restart_service_batch_threads_service(self):
        from execution.skills import kaseya_restart_service as rs
        fake = self._fake()
        r = rs.run(_ctx(fake), machines=["iwr-01", "iwr-02"], service="Spooler")
        self.assertEqual(r["machines_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        for row in r["results"]:
            self.assertTrue(row["ok"], row)
            self.assertEqual(row["restarting_service"], "Spooler")
            self.assertIn("machine", row)
        # bad service name is rejected per-row, still attributable
        f2 = self._fake()
        bad = rs.run(_ctx(f2), machines=["iwr-01"], service="Spooler && evil")
        self.assertEqual(bad["machines_done"], 1)
        self.assertEqual(bad["ok_count"], 0)
        self.assertFalse(bad["results"][0]["ok"])
        self.assertEqual(bad["results"][0]["machine"], "iwr-01")
        self.assertEqual(f2.writes, [])               # nothing scheduled

    def test_run_procedure_batch(self):
        from execution.skills import kaseya_run_procedure as rp
        procs = _env([{"AgentProcedureId": 77, "AgentProcedureName": "Clear Temp"}])
        fake = FakeKaseya({"/automation/agentprocs": procs})
        r = rp.run(_ctx(fake), machines=["iwr-01", "iwr-02"], procedure="Clear Temp")
        self.assertEqual(r["machines_done"], 2)
        self.assertEqual(r["ok_count"], 2)
        for row in r["results"]:
            self.assertTrue(row["ok"], row)
            self.assertEqual(row["procedure"], "Clear Temp")
            self.assertIn("machine", row)
        # single path
        one = rp.run(_ctx(fake), machine="iwr-01", procedure="Clear Temp")
        self.assertTrue(one["ok"])
        self.assertNotIn("results", one)


if __name__ == "__main__":
    unittest.main()
