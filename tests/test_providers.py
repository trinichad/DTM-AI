"""LLM provider tests — request/response translation + tool-use, no network."""
import json
import tempfile
import unittest
from pathlib import Path

from execution.core.config import Config
from execution.core.router import (ClaudeProvider, CodexProvider, MockProvider,
                                    ModelRouter, OpenAIProvider, OllamaProvider)

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


class Codex(unittest.TestCase):
    """ChatGPT-plan provider (D-26) — Responses API wire shape + SSE parse, no network."""

    def test_request_shape_and_stream_parse(self):
        captured = {}

        def st(method, url, headers=None, params=None, json_body=None, timeout=120):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json_body
            yield 'data: {"type":"response.reasoning_summary_text.delta","delta":"hmm"}'
            yield 'data: {"type":"response.output_text.delta","delta":"2 "}'
            yield 'data: {"type":"response.output_text.delta","delta":"assets"}'
            yield ('data: {"type":"response.output_item.done","item":{"type":"function_call",'
                   '"call_id":"call_1","name":"kaseya_list_assets","arguments":"{\\"a\\":1}"}}')
            yield 'data: {"type":"response.completed","response":{}}'

        ch = {"content": [], "thinking": []}
        p = CodexProvider(lambda: ("tok-abc", "acct-1"), stream_transport=st)
        res = p.chat_stream(NEUTRAL, TOOLS, "gpt-5.5",
                            lambda t, k="content": ch[k].append(t))
        self.assertTrue(captured["url"].endswith("/responses"))
        self.assertEqual(captured["headers"]["Authorization"], "Bearer tok-abc")
        self.assertEqual(captured["headers"]["chatgpt-account-id"], "acct-1")
        body = captured["body"]
        self.assertTrue(body["stream"])                       # backend mandates streaming
        self.assertEqual(body["instructions"], "be helpful")  # system hoisted to instructions
        # tools flattened to Responses shape (no nested "function")
        self.assertEqual(body["tools"][0]["name"], "kaseya_list_assets")
        self.assertNotIn("function", body["tools"][0])
        # function_call + function_call_output round-trip with the SAME call_id
        kinds = [(i["type"], i.get("call_id")) for i in body["input"]]
        self.assertIn(("function_call", "c1"), kinds)
        self.assertIn(("function_call_output", "c1"), kinds)
        # reasoning surfaced on the thinking channel, kept out of content
        self.assertEqual(ch["thinking"], ["hmm"])
        self.assertEqual(res.content, "2 assets")
        self.assertEqual(res.tool_calls[0], {"id": "call_1", "name": "kaseya_list_assets",
                                             "arguments": {"a": 1}})
        self.assertFalse(res.is_local)

    def test_chat_aggregates_stream(self):
        def st(method, url, headers=None, params=None, json_body=None, timeout=120):
            yield 'data: {"type":"response.output_text.delta","delta":"PONG"}'
            yield 'data: {"type":"response.completed","response":{}}'
        res = CodexProvider(lambda: ("t", "a"), stream_transport=st).chat(NEUTRAL, [], "gpt-5.5")
        self.assertEqual(res.content, "PONG")


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


class Streaming(unittest.TestCase):
    def test_ollama_ndjson_stream(self):
        def st(method, url, headers=None, params=None, json_body=None, timeout=120):
            self.assertTrue(json_body["stream"])
            for l in ['{"message":{"content":"Hel"}}', '{"message":{"content":"lo"}}',
                      '{"message":{"content":""},"done":true}']:
                yield l
        deltas = []
        res = OllamaProvider("http://x", stream_transport=st).chat_stream(
            [{"role": "user", "content": "hi"}], [], "m", lambda t, k="content": deltas.append(t))
        self.assertEqual(deltas, ["Hel", "lo"])
        self.assertEqual(res.content, "Hello")

    def test_ollama_reasoning_thinking_channel(self):
        # reasoning models (qwen3.5) stream chain-of-thought in `thinking` BEFORE the answer.
        # thinking is emitted on its own channel and kept OUT of the persisted content.
        def st(method, url, headers=None, params=None, json_body=None, timeout=120):
            for l in ['{"message":{"content":"","thinking":"Let me "}}',
                      '{"message":{"content":"","thinking":"count."}}',
                      '{"message":{"content":"1 2 3"}}',
                      '{"message":{"content":""},"done":true}']:
                yield l
        ch = {"content": [], "thinking": []}
        res = OllamaProvider("http://x", stream_transport=st).chat_stream(
            [], [], "qwen3.5:27b", lambda t, k="content": ch[k].append(t))
        self.assertEqual(ch["thinking"], ["Let me ", "count."])   # reasoning surfaced live
        self.assertEqual(ch["content"], ["1 2 3"])
        self.assertEqual(res.content, "1 2 3")                    # only the answer is returned/persisted

    def test_ollama_stream_tool_calls(self):
        def st(method, url, headers=None, params=None, json_body=None, timeout=120):
            yield ('{"message":{"content":"","tool_calls":'
                   '[{"function":{"name":"kaseya_list_assets","arguments":{}}}]},"done":true}')
        res = OllamaProvider("http://x", stream_transport=st).chat_stream([], [], "m", lambda t: None)
        self.assertEqual(res.tool_calls[0]["name"], "kaseya_list_assets")

    def test_claude_sse_stream_text_and_tooluse(self):
        lines = [
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"42 "}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"assets"}}',
            'data: {"type":"content_block_stop","index":0}',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"t1","name":"kaseya_list_assets"}}',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"a\\":1}"}}',
            'data: {"type":"content_block_stop","index":1}',
            'data: {"type":"message_stop"}',
        ]
        def st(method, url, headers=None, params=None, json_body=None, timeout=120):
            self.assertTrue(json_body["stream"])
            yield from lines
        deltas = []
        res = ClaudeProvider("k", stream_transport=st).chat_stream(
            [{"role": "user", "content": "hi"}], [], "claude-opus-4-8", lambda t, k="content": deltas.append(t))
        self.assertEqual("".join(deltas), "42 assets")
        self.assertEqual(res.tool_calls[0]["name"], "kaseya_list_assets")
        self.assertEqual(res.tool_calls[0]["arguments"], {"a": 1})

    def test_mock_stream_emits_whole(self):
        got = []
        res = MockProvider().chat_stream([{"role": "user", "content": "hey"}], [], "m",
                                         lambda t, k="content": got.append(t))
        self.assertEqual(got, [res.content])


class Routing(unittest.TestCase):
    def test_only_local_when_no_keys(self):
        r = ModelRouter(_cfg(MSPAI_LOCAL_MODEL="llama3.1"))
        ms = r.available_models()
        self.assertTrue(all(m["local"] for m in ms))
        self.assertTrue(ms[0]["default"])

    def test_cloud_models_appear_when_key_set(self):
        r = ModelRouter(_cfg(MSPAI_LOCAL_MODEL="llama3.1", ANTHROPIC_API_KEY="sk-ant"))
        ids = {m["id"] for m in r.available_models()}
        self.assertIn("anthropic:claude-opus-4-8", ids)

    def test_cloud_hidden_when_disabled(self):
        r = ModelRouter(_cfg(ANTHROPIC_API_KEY="sk-ant", MSPAI_ALLOW_CLOUD="0"))
        self.assertTrue(all(m["local"] for m in r.available_models()))

    def test_resolve_falls_back_to_local_without_key(self):
        r = ModelRouter(_cfg(MSPAI_LOCAL_MODEL="llama3.1"))
        prov, model = r.resolve("anthropic:claude-opus-4-8")  # no key -> local
        self.assertIsInstance(prov, OllamaProvider)

    def test_resolve_codex_with_refresh_token(self):
        r = ModelRouter(_cfg(OPENAI_CODEX_REFRESH_TOKEN="rt.1.x"))
        prov, model = r.resolve("openai-codex:gpt-5.5")
        self.assertIsInstance(prov, CodexProvider)
        self.assertEqual(model, "gpt-5.5")
        ids = {m["id"] for m in r.available_models()}
        self.assertIn("openai-codex:gpt-5.5", ids)
        # without the token the model is hidden and resolve falls back to local
        r2 = ModelRouter(_cfg(MSPAI_LOCAL_MODEL="llama3.1"))
        self.assertIsInstance(r2.resolve("openai-codex:gpt-5.5")[0], OllamaProvider)

    def test_resolve_claude_with_key(self):
        r = ModelRouter(_cfg(ANTHROPIC_API_KEY="sk-ant"))
        prov, model = r.resolve("anthropic:claude-opus-4-8")
        self.assertIsInstance(prov, ClaudeProvider)
        self.assertEqual(model, "claude-opus-4-8")

    def test_ollama_sends_configured_num_ctx(self):
        cap = {}
        def fake(method, url, headers=None, params=None, json_body=None, **kw):
            cap["body"] = json_body
            return 200, {"message": {"content": "ok"}}
        r = ModelRouter(_cfg(MSPAI_LOCAL_MODEL="qwen3.5:27b", MSPAI_OLLAMA_NUM_CTX="16384"))
        self.assertEqual(r.ollama_num_ctx, 16384)
        prov, model = r.resolve(None)              # local fallback
        prov._t = fake
        prov.chat([{"role": "user", "content": "hi"}], [], model)
        self.assertEqual(cap["body"]["options"]["num_ctx"], 16384)

    def test_history_limits_configurable(self):
        r = ModelRouter(_cfg(MSPAI_MAX_HISTORY_CHARS="40000", MSPAI_MAX_HISTORY_MSGS="50"))
        self.assertEqual(r.history_chars, 40000)
        self.assertEqual(r.history_msgs, 50)

    def test_budget_is_model_aware(self):
        # D-50: budget scales with the MODEL's window (≤ half), capped by MSPAI_CLOUD_HISTORY_CHARS.
        r = ModelRouter(_cfg(MSPAI_OLLAMA_NUM_CTX="16384"))
        self.assertEqual(r.budget_for("ollama"), 32768)        # bounded by num_ctx (*2 chars/token)
        # claude-opus window 200k tok → 800k chars → half = 400k, capped at the 240k default
        self.assertEqual(r.budget_for("anthropic", "claude-opus-4-8"), 240000)
        # gpt-4o window 128k tok → 512k chars → half = 256k, still capped at 240k
        self.assertEqual(r.budget_for("openai", "gpt-4o"), 240000)

    def test_model_context_windows_in_catalog(self):
        # D-50: each model advertises its real context window (tokens) for the chat meter.
        r = ModelRouter(_cfg(ANTHROPIC_API_KEY="sk-ant", OPENAI_CODEX_REFRESH_TOKEN="t",
                             MSPAI_OLLAMA_NUM_CTX="16384"))
        by_id = {m["id"]: m for m in r.available_models()}
        self.assertEqual(by_id[f"ollama:{r.local_model}"]["context_tokens"], 16384)
        self.assertEqual(by_id["anthropic:claude-opus-4-8"]["context_tokens"], 200_000)
        self.assertEqual(by_id["openai-codex:gpt-5.5"]["context_tokens"], 400_000)
        # context_chars (meter denominator) = window tokens * 4
        self.assertEqual(by_id["openai-codex:gpt-5.5"]["context_chars"], 1_600_000)

    def test_history_override_applies_to_all_models(self):
        r = ModelRouter(_cfg(MSPAI_MAX_HISTORY_CHARS="5000", MSPAI_OLLAMA_NUM_CTX="16384"))
        self.assertEqual(r.budget_for("ollama"), 5000)
        self.assertEqual(r.budget_for("anthropic"), 5000)


if __name__ == "__main__":
    unittest.main()
