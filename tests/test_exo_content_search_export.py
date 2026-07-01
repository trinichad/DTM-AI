"""exo_content_search_export (D-116) — start export, then download server-side.

Proves the lifecycle: no export action + search Completed → starts an export with -Export (NEVER
-Purge); search estimate not done → "wait"; export action exists but InProgress → "still
preparing"; export Completed → reads the SAS credentials and downloads server-side, returning a
download link WITHOUT leaking the SAS; missing credentials and over-cap are clean errors.
"""
import unittest

from execution.core.context import ToolContext


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


_CREDS = ("Container url: https://acct.blob.core.windows.net/exp123; "
          "SAS token: ?sv=2022-11-02&ss=b&sig=SECRETSIG; Estimated items: 5")


class FakeEXO:
    def __init__(self, search_status="Completed", export_action=None):
        self.calls = []
        self.search_status = search_status
        self.export_action = export_action            # None = no export action yet

    def invoke_compliance(self, cmdlet, params=None):
        params = params or {}
        self.calls.append((cmdlet, dict(params)))
        if cmdlet == "Get-ComplianceSearchAction":
            return [self.export_action] if self.export_action else {"error": "the action wasn't found"}
        if cmdlet == "Get-ComplianceSearch":
            return [{"Name": params.get("Identity"), "Status": self.search_status}] \
                if self.search_status else {"error": "couldn't be found"}
        if cmdlet == "New-ComplianceSearchAction":
            return {"ok": True}
        return {"error": f"unexpected cmdlet {cmdlet}"}

    def _last(self, cmdlet):
        return [c for c in self.calls if c[0] == cmdlet]


class ExportContentSearch(unittest.TestCase):
    def test_starts_export_when_complete_and_no_action(self):
        from execution.skills import exo_content_search_export as mod
        fake = FakeEXO(search_status="Completed", export_action=None)
        r = mod.run(_ctx(fake), name="hunt")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["export_status"], "Starting")
        start = fake._last("New-ComplianceSearchAction")
        self.assertTrue(start)
        # only SearchName + Export — NEVER Purge
        self.assertEqual(set(start[0][1]), {"SearchName", "Export"})
        self.assertNotIn("Purge", start[0][1])

    def test_waits_when_estimate_not_done(self):
        from execution.skills import exo_content_search_export as mod
        fake = FakeEXO(search_status="InProgress", export_action=None)
        r = mod.run(_ctx(fake), name="hunt")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["search_status"], "InProgress")
        self.assertFalse(fake._last("New-ComplianceSearchAction"))

    def test_still_preparing_when_export_incomplete(self):
        from execution.skills import exo_content_search_export as mod
        fake = FakeEXO(export_action={"Status": "InProgress", "Results": ""})
        r = mod.run(_ctx(fake), name="hunt")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["export_status"], "InProgress")

    def test_completed_downloads_and_hides_sas(self):
        from execution.skills import exo_content_search_export as mod
        from execution.clients import azure_blob
        fake = FakeEXO(export_action={"Status": "Completed", "Results": _CREDS})

        captured = {}

        def fake_download(container, token, dest_dir, *, max_bytes, on_progress=None,
                          lister=None, downloader=None):
            captured["container"] = container
            captured["token"] = token
            return {"dir": dest_dir, "blob_count": 3, "total_bytes": 4096, "files": []}

        orig = azure_blob.download_container
        azure_blob.download_container = fake_download
        try:
            r = mod.run(_ctx(fake), name="hunt")
        finally:
            azure_blob.download_container = orig

        self.assertTrue(r["ok"], r)
        self.assertEqual(r["export_status"], "Completed")
        self.assertEqual(r["files"], 3)
        self.assertTrue(r["download_url"].startswith("/api/fs/download?path="))
        # the SAS was passed to the downloader but is NOT present anywhere in the tool result
        self.assertEqual(captured["token"], "?sv=2022-11-02&ss=b&sig=SECRETSIG")
        self.assertNotIn("SECRETSIG", repr(r))

    def test_completed_but_missing_credentials_is_clean_error(self):
        from execution.skills import exo_content_search_export as mod
        fake = FakeEXO(export_action={"Status": "Completed", "Results": "Estimated items: 5"})
        r = mod.run(_ctx(fake), name="hunt")
        self.assertFalse(r["ok"])
        self.assertEqual(r["step"], "credentials")

    def test_over_cap_is_clean_error(self):
        from execution.skills import exo_content_search_export as mod
        from execution.clients import azure_blob
        fake = FakeEXO(export_action={"Status": "Completed", "Results": _CREDS})

        def boom(*a, **k):
            raise ValueError("export is ~9999999999 bytes, over the cap")

        orig = azure_blob.download_container
        azure_blob.download_container = boom
        try:
            r = mod.run(_ctx(fake), name="hunt")
        finally:
            azure_blob.download_container = orig
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("too_large"))

    def test_missing_search_is_clean_error(self):
        from execution.skills import exo_content_search_export as mod
        fake = FakeEXO(search_status=None, export_action=None)
        r = mod.run(_ctx(fake), name="ghost")
        self.assertFalse(r["ok"])
        self.assertEqual(r["step"], "locate")


if __name__ == "__main__":
    unittest.main()
