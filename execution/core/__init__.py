"""MSP AI secure core — stdlib-only. No third-party imports at module load.

Modules:
  context      ToolContext: the per-call security envelope (tenant, actor, clients).
  validation   minimal JSON-Schema validator for tool PARAMETERS (zero-dep).
  config       secure config loader: 0600 enforcement, fingerprints, fail-closed.
  credentials  CredentialSpec registry + require() (the only path to a vendor client).
  audit        append-only audit log (every call, reads included).
  registry     auto-discovery skills registry (drop a file in skills/ -> live).
  dispatch     the guardrail chokepoint: validate args + enforce CATEGORY + approval + audit.
  router       model router: local-first provider selection (Ollama default; cloud opt-in).
"""
