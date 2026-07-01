"""m365_offboard_user (D-112 follow-up) — the composite offboard streams live per-step progress.

The offboard is one long write call; without ctx.progress the dashboard shows only "Waiting". This
proves each enabled step reports a labelled progress tick and the bar is closed at the end.
"""
import unittest

from execution.core.context import ToolContext


class FakeM365:
    """Minimal Graph client: serves the user lookup and accepts the session-revoke POST."""
    def get(self, path, params=None):
        if path.startswith("/users/"):
            return {"id": "u1", "userPrincipalName": "bob@x.com", "onPremisesSyncEnabled": False}
        return {}

    def post(self, path, body=None):
        return {}                          # revokeSignInSessions → empty success

    def patch(self, path, body=None):
        return {}                          # password reset / accountEnabled → empty success


class OffboardProgress(unittest.TestCase):
    def _ctx_recording(self):
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: FakeM365())
        calls = []
        ctx.progress = lambda done, total=0, label="": calls.append((done, total, label))
        return ctx, calls

    def test_each_step_ticks_and_bar_closes(self):
        from execution.skills import m365_offboard_user as mod
        ctx, calls = self._ctx_recording()
        # isolate the mechanism: only the cloud session-revoke step is on (total = 1)
        r = mod.run(ctx, user="bob@x.com", sign_out_devices=True, reset_password=False,
                    block_signin=False, convert_to_shared=False, remove_licenses=False,
                    hide_from_gal=False, prefix_display_name=False, list_groups=False,
                    list_mailbox_access=False)
        self.assertTrue(r["ok"], r)
        labels = [c[2] for c in calls]
        self.assertIn("signing out of all devices", labels)
        self.assertEqual(calls[0], (1, 1, "signing out of all devices"))   # 1/1 while running
        self.assertEqual(calls[-1], (1, 1, ""))                            # bar closed at the end

    def test_total_reflects_only_enabled_steps(self):
        from execution.skills import m365_offboard_user as mod
        ctx, calls = self._ctx_recording()
        r = mod.run(ctx, user="bob@x.com", sign_out_devices=True, reset_password=True,
                    block_signin=True, convert_to_shared=False, remove_licenses=False,
                    hide_from_gal=False, prefix_display_name=False, list_groups=False,
                    list_mailbox_access=False)
        self.assertTrue(r["ok"], r)
        totals = {c[1] for c in calls}
        self.assertEqual(totals, {3})                                      # exactly 3 enabled steps


if __name__ == "__main__":
    unittest.main()
