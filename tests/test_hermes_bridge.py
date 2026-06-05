"""HermesBridge tests — SSE parsing, tool-frame mapping, model_info, availability.

We drive the bridge with a fake urlopen so no network/Hermes is needed; the frames mirror
the real api_server output (chat.completion.chunk deltas + event: hermes.tool.progress)."""
import tempfile
import unittest
import urllib.request
from pathlib import Path

from execution.core.hermes_bridge import HermesBridge, _clean_tool, _iter_sse


class StubCfg:
    def __init__(self, d): self.d = d
    def get(self, k, default=None): return self.d.get(k, default)


class FakeResp:
    def __init__(self, lines): self._lines = lines
    def __iter__(self): return iter(self._lines)
    def read(self): return b"".join(self._lines)


# Real-shape frames captured from the api_server (one tool call + streamed answer).
SSE_LINES = [
    b'data: {"choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}\n',
    b"\n",
    b"event: hermes.tool.progress\n",
    b'data: {"tool":"mcp_dtm_all_system_health","status":"running","toolCallId":"c1"}\n',
    b"\n",
    b"event: hermes.tool.progress\n",
    b'data: {"tool":"mcp_dtm_all_system_health","status":"completed","toolCallId":"c1"}\n',
    b"\n",
    b'data: {"choices":[{"delta":{"content":"DTM "}}]}\n',
    b"\n",
    b'data: {"choices":[{"delta":{"content":"AI is ok."}}]}\n',
    b"\n",
    b"data: [DONE]\n",
    b"\n",
]


class CleanTool(unittest.TestCase):
    def test_strip_mcp_namespacing(self):
        self.assertEqual(_clean_tool("mcp_dtm_all_system_health"), "system_health")
        self.assertEqual(_clean_tool("mcp_dtm_acme_kaseya_list_assets"), "kaseya_list_assets")
        self.assertEqual(_clean_tool("mcp_notion_search"), "search")
        self.assertEqual(_clean_tool("system_health"), "system_health")


class SseParse(unittest.TestCase):
    def test_frames(self):
        frames = list(_iter_sse(FakeResp(SSE_LINES)))
        # first: plain data (role delta), then two tool events, two content deltas, then [DONE]
        self.assertEqual(frames[0][0], None)            # no event line
        self.assertEqual(frames[1][0], "hermes.tool.progress")
        self.assertIn("running", frames[1][1])
        self.assertEqual(frames[-1][1], "[DONE]")


class Availability(unittest.TestCase):
    def test_requires_key(self):
        self.assertFalse(HermesBridge(StubCfg({})).available)
        self.assertTrue(HermesBridge(StubCfg({"HERMES_API_KEY": "k"})).available)


class StreamFlow(unittest.TestCase):
    def setUp(self):
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None: FakeResp(SSE_LINES)

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def test_stream_emits_tool_and_delta_frames(self):
        b = HermesBridge(StubCfg({"HERMES_API_KEY": "k"}))
        events = []
        answer = b.stream("hi", "sess-1", events.append)
        self.assertEqual(answer, "DTM AI is ok.")
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "tool_result", "delta", "delta"])
        self.assertEqual(events[0]["name"], "system_health")   # namespacing stripped
        self.assertTrue(events[1]["ok"])
        # delta frames use "text" (matches the UI + direct engine), not "content"
        self.assertEqual(events[2]["text"], "DTM ")
        self.assertNotIn("content", events[2])

    def test_session_header_set(self):
        captured = {}
        def fake(req, timeout=None):
            captured["sid"] = req.headers.get("X-hermes-session-id")
            captured["auth"] = req.headers.get("Authorization")
            return FakeResp(SSE_LINES)
        urllib.request.urlopen = fake
        HermesBridge(StubCfg({"HERMES_API_KEY": "secret"})).stream("hi", "conv-9", lambda e: None)
        self.assertEqual(captured["sid"], "conv-9")
        self.assertEqual(captured["auth"], "Bearer secret")


class ModelInfo(unittest.TestCase):
    def test_reads_config_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config.yaml").write_text(
                "model:\n  default: gpt-5.5\n  provider: openai-codex\n"
                "  base_url: https://x\nproviders: {}\n")
            b = HermesBridge(StubCfg({"DTM_HERMES_DATA_DIR": d}))
            mi = b.model_info()
            self.assertEqual(mi["model"], "gpt-5.5")
            self.assertEqual(mi["provider"], "openai-codex")
            self.assertEqual(mi["provider_label"], "OpenAI Codex")

    def test_missing_config_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(HermesBridge(StubCfg({"DTM_HERMES_DATA_DIR": d})).model_info())


if __name__ == "__main__":
    unittest.main()
