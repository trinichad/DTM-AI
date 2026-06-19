"""MS Teams bot tests (D-29) — the security boundaries are real, not prose.

Proves: service-URL host allowlist (anti token-exfil), conversation-id validation,
default-deny user allowlist (Hermes model), JWT rejection path, dedup, and that a
group message without an @mention is ignored.
"""
import unittest
from unittest import mock

from execution.clients.msteams import (
    TeamsAuthError, TeamsClient, parse_allowlist, user_allowed, validate_service_url)
from execution.core.teams_bot import TeamsBridge, _Dedup


class ServiceUrlAllowlist(unittest.TestCase):
    def test_known_hosts_pass(self):
        self.assertEqual(validate_service_url("https://smba.trafficmanager.net/teams/"),
                         "https://smba.trafficmanager.net/teams/")
        self.assertTrue(validate_service_url("https://smba.infra.gov.teams.microsoft.us/x"))

    def test_unknown_hosts_blocked(self):
        self.assertIsNone(validate_service_url("https://evil.example/teams/"))
        self.assertIsNone(validate_service_url("http://smba.trafficmanager.net/teams/"))
        self.assertIsNone(validate_service_url(""))


class Allowlist(unittest.TestCase):
    def test_default_deny_with_no_config(self):
        ok, reason = user_allowed({}, "aad-123")
        self.assertFalse(ok)
        self.assertIn("no users are allowed", reason)

    def test_listed_user_allowed_others_denied(self):
        env = {"TEAMS_ALLOWED_USERS": "aad-123|Alex, aad-456"}
        self.assertTrue(user_allowed(env, "aad-123")[0])
        self.assertTrue(user_allowed(env, "aad-456")[0])
        self.assertFalse(user_allowed(env, "aad-789")[0])

    def test_allow_all_is_explicit(self):
        self.assertTrue(user_allowed({"TEAMS_ALLOW_ALL_USERS": "true"}, "anyone")[0])
        self.assertFalse(user_allowed({"TEAMS_ALLOW_ALL_USERS": "no"}, "anyone")[0])

    def test_parse_entries(self):
        self.assertEqual(parse_allowlist("a|Alice, b"),
                         [{"id": "a", "name": "Alice", "user": ""},
                          {"id": "b", "name": "", "user": ""}])


class ClientUrls(unittest.TestCase):
    def _client(self):
        c = TeamsClient("cid", "sec", "tid")
        c._get_token = lambda: "tok"
        return c

    def test_conv_id_charset_enforced(self):
        c = self._client()
        with self.assertRaises(ValueError):
            c._activities_url("19:abc/../../evil")
        with self.assertRaises(ValueError):
            c._activities_url("")

    def test_send_builds_allowlisted_url(self):
        calls = []
        c = TeamsClient("cid", "sec", "tid",
                        transport=lambda m, u, headers=None, params=None, json_body=None, **_:
                        calls.append((m, u, json_body)) or (200, {"id": "m1"}))
        c._get_token = lambda: "tok"
        r = c.send_text("19:chat@thread.tacv2", "hello",
                        service_url="https://smba.trafficmanager.net/amer/")
        self.assertTrue(r["ok"])
        self.assertEqual(calls[0][1],
                         "https://smba.trafficmanager.net/amer/v3/conversations/"
                         "19:chat@thread.tacv2/activities")

    def test_tampered_service_url_falls_back_to_default(self):
        c = self._client()
        url = c._activities_url("19:x", service_url="https://evil.example/")
        self.assertTrue(url.startswith("https://smba.trafficmanager.net/teams/"))

    def test_requires_secret_or_cert(self):
        import os
        os.environ["MSPAI_TEAMS_CERT_PATH"] = "/nonexistent/teams_cert.pem"
        try:
            with self.assertRaises(ValueError):
                TeamsClient("cid", "", "tid")
        finally:
            os.environ.pop("MSPAI_TEAMS_CERT_PATH", None)


def _activity(**over):
    base = {"type": "message", "id": "act-1", "text": "hello bot",
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
            "from": {"aadObjectId": "aad-123", "name": "Alex"},
            "conversation": {"id": "19:chat@thread.tacv2", "conversationType": "personal"}}
    base.update(over)
    return base


class Bridge(unittest.TestCase):
    def _bridge(self, env, verify=lambda *a: {"aud": "cid"}):
        agent = mock.Mock()
        agent.audit = mock.Mock()
        b = TeamsBridge(agent, verify_jwt=verify)
        b._env = lambda: env
        b.enabled = lambda: True
        b._client = mock.Mock()
        b._run_turn = mock.Mock()      # don't run a real agent turn in unit tests
        return b

    def test_bad_jwt_rejected_401(self):
        def boom(*a):
            raise TeamsAuthError("nope")
        b = self._bridge({"TEAMS_CLIENT_ID": "cid", "TEAMS_ALLOW_ALL_USERS": "true"}, verify=boom)
        status, body = b.handle("Bearer x", _activity())
        self.assertEqual(status, 401)
        b._run_turn.assert_not_called()

    def test_unconfigured_404(self):
        b = self._bridge(None)
        self.assertEqual(b.handle("", _activity())[0], 404)

    def test_denied_user_never_reaches_agent(self):
        b = self._bridge({"TEAMS_CLIENT_ID": "cid", "TEAMS_ALLOWED_USERS": "aad-OTHER"})
        status, body = b.handle("Bearer x", _activity())
        self.assertEqual(status, 200)
        self.assertTrue(body.get("denied"))
        b._run_turn.assert_not_called()

    def test_empty_allowlist_denies_everyone(self):
        b = self._bridge({"TEAMS_CLIENT_ID": "cid"})
        status, body = b.handle("Bearer x", _activity())
        self.assertTrue(body.get("denied"))

    def test_allowed_user_starts_turn(self):
        b = self._bridge({"TEAMS_CLIENT_ID": "cid", "TEAMS_ALLOWED_USERS": "aad-123|Alex"})
        with mock.patch("execution.core.teams_bot.threading.Thread") as th:
            status, body = b.handle("Bearer x", _activity())
        self.assertEqual(status, 202)
        th.assert_called_once()

    def test_dedup_second_delivery(self):
        b = self._bridge({"TEAMS_CLIENT_ID": "cid", "TEAMS_ALLOW_ALL_USERS": "true"})
        with mock.patch("execution.core.teams_bot.threading.Thread"):
            b.handle("Bearer x", _activity())
            status, body = b.handle("Bearer x", _activity())
        self.assertTrue(body.get("deduped"))

    def test_group_message_without_mention_ignored(self):
        b = self._bridge({"TEAMS_CLIENT_ID": "cid", "TEAMS_ALLOW_ALL_USERS": "true"})
        act = _activity(conversation={"id": "19:x", "conversationType": "groupChat"})
        status, body = b.handle("Bearer x", act)
        self.assertEqual(body.get("ignored"), "group message without @mention")

    def test_group_message_with_mention_processed(self):
        b = self._bridge({"TEAMS_CLIENT_ID": "cid", "TEAMS_ALLOW_ALL_USERS": "true"})
        act = _activity(text="<at>MSP AI</at> status?",
                        conversation={"id": "19:x", "conversationType": "groupChat"},
                        entities=[{"type": "mention", "mentioned": {"id": "28:cid"}}])
        with mock.patch("execution.core.teams_bot.threading.Thread") as th:
            status, _ = b.handle("Bearer x", act)
        self.assertEqual(status, 202)

    def test_non_message_activity_ignored(self):
        b = self._bridge({"TEAMS_CLIENT_ID": "cid", "TEAMS_ALLOW_ALL_USERS": "true"})
        status, body = b.handle("Bearer x", _activity(type="conversationUpdate"))
        self.assertEqual(body.get("ignored"), "conversationUpdate")

    def test_dedup_lru(self):
        d = _Dedup(size=2)
        self.assertFalse(d.seen("a")); self.assertTrue(d.seen("a"))
        d.seen("b"); d.seen("c")               # 'a' evicted
        self.assertFalse(d.seen("a"))


if __name__ == "__main__":
    unittest.main()
