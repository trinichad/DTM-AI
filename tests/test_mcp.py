"""MCP server tests — the fence holds: tools are exposed and calls go through dispatch."""
import json
import tempfile
import unittest
from pathlib import Path

from execution.mcp_server import DtmMcpServer


class McpFence(unittest.TestCase):
    def setUp(self):
        # isolate the sqlite so tests don't touch the project db
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = DtmMcpServer(tenant_id="acme", actor="hermes")
        # point the agent's stores at a temp db
        from execution.runtime import build_agent
        self.srv.agent = build_agent(db_path=Path(self.tmp.name) / "m.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_initialize(self):
        r = self.srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(r["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(r["result"]["serverInfo"]["boundTenant"], "acme")

    def test_initialized_notification_has_no_reply(self):
        self.assertIsNone(self.srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_tools_list_exposes_registry(self):
        r = self.srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in r["result"]["tools"]}
        self.assertIn("system_health", names)
        self.assertIn("kaseya_list_assets", names)
        # each tool carries an inputSchema
        for t in r["result"]["tools"]:
            self.assertIn("inputSchema", t)

    def test_tool_call_goes_through_dispatch(self):
        r = self.srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "system_health", "arguments": {}}})
        self.assertFalse(r["result"]["isError"])
        env = json.loads(r["result"]["content"][0]["text"])
        self.assertTrue(env["ok"])
        self.assertEqual(env["tenant_id"], "acme")

    def test_unknown_method(self):
        r = self.srv.handle({"jsonrpc": "2.0", "id": 4, "method": "frobnicate"})
        self.assertEqual(r["error"]["code"], -32601)

    def test_call_cannot_override_tenant(self):
        # attempt to smuggle a different tenant in arguments -> ignored, stays bound to acme
        r = self.srv.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                             "params": {"name": "system_health",
                                        "arguments": {"tenant_id": "other-client"}}})
        env = json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(env["tenant_id"], "acme")


if __name__ == "__main__":
    unittest.main()
