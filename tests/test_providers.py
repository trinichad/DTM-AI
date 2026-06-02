"""LLM provider tests — request/response translation + tool-use, no network."""
import json
import tempfile
import unittest
from pathlib import Path

from execution.core.config import Config
from execution.core.router import ClaudeProvider, ModelRouter, OpenAIProvider, OllamaProvider

NEUTRAL = [
    {"role": "system", "content": "be helpful"},
    {"role": "user", "content": "how many assets?"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "kaseya_list_assets", "arguments": {}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "kaseya_list_assets", "content": '{"ok":true,"data":[1,2]}'},
]
TOOLS = [{"type": "function", "function": {"name": "kaseya_list_assets", "description": "list",
                                           "parameters": {"type": "object", "properties": {}}}}]


def _cfg(**pairs):
    d = tempfile.mkdtemp()
    p = Path(d) / ".env"
    p.write_text("\n".join(f"{k}={v}" for k, v in pairs.items()), encoding="utf-8")
    p.chmod(0o600)
    return Config(env_path=p)


class OpenAI(unittest.TestCase):
    def test_request_and_tool_call_parse(self):
        captured = {}

        def fake(method, url, headers=None, params=None, json_body=None, **kw):
            captured["url"] = url
            captured["body"] = json_body
            captured["auth"] = headers.get("Authorization")
            return 200, {"choices": [{"message": {"content": "",
                "tool_calls": [{"id": "x", "type": "function",
                                "function": {"name": "kaseya_list_assets", "arguments": "{}"}}]}}]}

        p = OpenAIProvider("sk-test", transport=fake)
        res = p.chat(NEUTRAL, TOOLS, "gpt-4o")
        self.assertTrue(captured["url"].endswith("/chat/completions"))
        self.assertEqual(captured["auth"], "Bearer sk-test")
        # assistant tool_calls translated to OpenAI shape (arguments as JSON string)
        asst = [m for m in captured["body"]["messages"] if m["role"] == "assistant"][0]
        self.assertEqual(asst["tool_calls"][0]["function"]["arguments"], "{}")
        # tool result carries tool_call_id
        toolmsg = [m for m in captured["body"]["messages"] if m["role"] == "tool"][0]
        self.assertEqual(toolmsg["tool_call_id"], "c1")
        self.assertEqual(res.tool_calls[0]["name"], "kaseya_list_assets")


class Claude(unittest.TestCase):
    def test_request_shape_and_tool_use_parse(self):
        captured = {}

        def fake(method, url, headers=None, params=None, json_body=None, **kw):
            captured["url"] = url
            captured["body"] = json_body
            captured["headers"] = headers
            return 200, {"content": [{"type": "text", "text": "There are 2 assets."}]}

        p = ClaudeProvider("sk-ant", transport=fake)
        res = p.chat(NEUTRAL, TOOLS, "claude-opus-4-8")
        self.assertTrue(captured["url"].endswith("/messages"))
        self.assertEqual(captured["headers"]["x-api-key"], "sk-ant")
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")
        # system hoisted out of messages
        self.assertEqual(captured["body"]["system"], "be helpful")
        # tools converted to input_schema
        self.assertIn("input_schema", captured["body"]["tools"][0])
        # assistant tool_use + tool_result blocks present
        roles = [m["role"] for m in captured["body"]["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])  # tool result folded into a user turn
        asst = captured["body"]["messages"][1]
        self.assertEqual(asst["content"][0]["type"], "tool_use")
        tr = captured["body"]["messages"][2]["content"][0]
        self.assertEqual(tr["type"], "tool_result")
        self.assertEqual(tr["tool_use_id"], "c1")
        self.assertEqual(res.content, "There are 2 assets.")

    def test_tool_use_response_parsed(self):
        def fake(method, url, headers=None, params=None, json_body=None, **kw):
            return 200, {"content": [{"type": "tool_use", "id": "t1", "name": "kaseya_list_assets", "input": {}}]}
        res = ClaudeProvider("k", transport=fake).chat(NEUTRAL, TOOLS, "claude-opus-4-8")
        self.assertEqual(res.tool_calls[0]["id"], "t1")
        self.assertFalse(res.is_local)


class Routing(unittest.TestCase):
    def test_only_local_when_no_keys(self):
        r = ModelRouter(_cfg(DTM_LOCAL_MODEL="llama3.1"))
        ms = r.available_models()
        self.assertTrue(all(m["local"] for m in ms))
        self.assertTrue(ms[0]["default"])

    def test_cloud_models_appear_when_key_set(self):
        r = ModelRouter(_cfg(DTM_LOCAL_MODEL="llama3.1", ANTHROPIC_API_KEY="sk-ant"))
        ids = {m["id"] for m in r.available_models()}
        self.assertIn("anthropic:claude-opus-4-8", ids)

    def test_cloud_hidden_when_disabled(self):
        r = ModelRouter(_cfg(ANTHROPIC_API_KEY="sk-ant", DTM_ALLOW_CLOUD="0"))
        self.assertTrue(all(m["local"] for m in r.available_models()))

    def test_resolve_falls_back_to_local_without_key(self):
        r = ModelRouter(_cfg(DTM_LOCAL_MODEL="llama3.1"))
        prov, model = r.resolve("anthropic:claude-opus-4-8")  # no key -> local
        self.assertIsInstance(prov, OllamaProvider)

    def test_resolve_claude_with_key(self):
        r = ModelRouter(_cfg(ANTHROPIC_API_KEY="sk-ant"))
        prov, model = r.resolve("anthropic:claude-opus-4-8")
        self.assertIsInstance(prov, ClaudeProvider)
        self.assertEqual(model, "claude-opus-4-8")


if __name__ == "__main__":
    unittest.main()
