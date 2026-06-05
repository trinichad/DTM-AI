"""MCP server tests — the fence holds: tools are exposed and calls go through dispatch."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from execution.mcp_server import DtmMcpServer, _path_tenant, make_http_handler


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

    def test_tool_call_caches_result_for_transcript(self):
        # the MCP path caches a result preview so the DTM AI transcript can show what Hermes saw
        self.srv.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                         "params": {"name": "system_health", "arguments": {}}})
        rows = self.srv.agent.audit.recent_results("hermes", 0)
        self.assertTrue(any(r["tool"] == "system_health" and r["data"] for r in rows))

    def test_handle_tenant_override_routes_per_request(self):
        # the same server instance answers for different tenants when the transport supplies one
        r = self.srv.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                             "params": {"name": "system_health", "arguments": {}}},
                            tenant="globex")
        env = json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(env["tenant_id"], "globex")
        # initialize reflects the per-request tenant too
        init = self.srv.handle({"jsonrpc": "2.0", "id": 7, "method": "initialize"}, tenant="globex")
        self.assertEqual(init["result"]["serverInfo"]["boundTenant"], "globex")


class PathTenant(unittest.TestCase):
    def test_routing(self):
        self.assertEqual(_path_tenant("/mcp"), "*")
        self.assertEqual(_path_tenant("/mcp/"), "*")
        self.assertEqual(_path_tenant("/mcp/acme"), "acme")
        self.assertEqual(_path_tenant("/mcp/acme/"), "acme")
        # not MCP endpoints / no traversal into sub-paths
        self.assertIsNone(_path_tenant("/"))
        self.assertIsNone(_path_tenant("/health"))
        self.assertIsNone(_path_tenant("/mcp/acme/extra"))


class HttpTransport(unittest.TestCase):
    """Spin the real HTTP transport on an ephemeral port and exercise it over the loopback."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = DtmMcpServer(tenant_id="*", actor="hermes")
        from execution.runtime import build_agent
        cls.srv.agent = build_agent(db_path=Path(cls.tmp.name) / "h.db")
        cls.token = "s3cret"
        handler = make_http_handler(cls.srv, token=cls.token)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    def _post(self, path, payload, token="s3cret"):
        url = f"http://127.0.0.1:{self.port}{path}"
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_health_get_needs_no_auth(self):
        url = f"http://127.0.0.1:{self.port}/health"
        resp = urllib.request.urlopen(url, timeout=5)
        self.assertEqual(resp.status, 200)
        self.assertTrue(json.loads(resp.read())["ok"])

    def test_tools_call_over_http(self):
        status, body = self._post("/mcp/acme", {"jsonrpc": "2.0", "id": 1,
                                                "method": "tools/call",
                                                "params": {"name": "system_health", "arguments": {}}})
        self.assertEqual(status, 200)
        env = json.loads(body["result"]["content"][0]["text"])
        self.assertTrue(env["ok"])
        self.assertEqual(env["tenant_id"], "acme")   # tenant bound by URL path

    def test_url_path_is_the_fence(self):
        # smuggling a tenant in args is still ignored — path wins
        status, body = self._post("/mcp/acme", {"jsonrpc": "2.0", "id": 2,
                                                "method": "tools/call",
                                                "params": {"name": "system_health",
                                                           "arguments": {"tenant_id": "evil"}}})
        env = json.loads(body["result"]["content"][0]["text"])
        self.assertEqual(env["tenant_id"], "acme")

    def test_missing_token_rejected(self):
        status, _ = self._post("/mcp/acme", {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                               token=None)
        self.assertEqual(status, 401)

    def test_bad_path_404(self):
        status, _ = self._post("/nope", {"jsonrpc": "2.0", "id": 4, "method": "ping"})
        self.assertEqual(status, 404)

    def test_notification_returns_202(self):
        status, body = self._post("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(status, 202)
        self.assertIsNone(body)


if __name__ == "__main__":
    unittest.main()
