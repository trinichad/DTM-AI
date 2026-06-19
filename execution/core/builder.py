"""Self-development engine (D-8) — draft new tools safely, never auto-live.

The AI drafts a candidate skill into a SANDBOX (skills_candidate/), where it is statically
validated (AST security scan + schema lint). Nothing runs and nothing reaches the live registry
until a human PROMOTES it. Generated tools are read-only by construction (write capability is a
separate, deliberate elevation). The static scanner is defense-in-depth — the human review +
read-only default + dispatch() guardrails are the real gates.

Flow:  draft(description) -> candidate file + validation  ->  human promote()/reject()
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANDIDATES_DIR = _PROJECT_ROOT / "skills_candidate"
SKILLS_DIR = _PROJECT_ROOT / "execution" / "skills"

# import allowlist for generated tools — data comes via ctx, not network/os libs
_ALLOWED_IMPORT_ROOTS = {
    "typing", "json", "re", "math", "datetime", "dataclasses", "collections",
    "execution",  # for execution.clients.scopes / execution.core.*
    "__future__",
}
# names that must never appear (calls or imports)
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input",
                    "globals", "locals", "vars", "getattr", "setattr", "delattr"}
_FORBIDDEN_IMPORT_ROOTS = {"os", "sys", "subprocess", "socket", "shutil", "importlib",
                           "ctypes", "pickle", "marshal", "requests", "urllib", "http",
                           "pty", "multiprocessing", "threading", "signal", "builtins",
                           "pathlib", "tempfile"}
_REQUIRED_ATTRS = ("NAME", "DESCRIPTION", "PARAMETERS")
# D-40: write candidates are allowed (REQUIRES_APPROVAL=True forced); destructive never.
_ALLOWED_CATEGORIES = ("read", "alert", "write")

PROMPT = """You write a single Python module for the MSP AI skill registry. Output ONLY the code
(no prose, no markdown fences). Follow this exact contract:

    \"\"\"<one-line docstring>\"\"\"
    from __future__ import annotations
    from typing import Any

    NAME = "<snake_case_unique_name>"
    DESCRIPTION = "<one line shown to the model>"
    SOURCE = "<kaseya|cylance|huntress|m365|msp_ai|or a custom integration id>"
    CATEGORY = "read"            # "read", "alert", or "write" — NEVER destructive
    RISK_LEVEL = "low"           # write tools: "medium" or "high"
    REQUIRES_APPROVAL = False    # write tools MUST set True (each run waits for owner sign-off)
    ENABLED_BY_DEFAULT = False   # generated tools start disabled
    PARAMETERS = {"type": "object", "properties": {...}, "additionalProperties": False}

    def run(ctx, **kwargs):
        # Get vendor data via the scoped connectors:
        #   from execution.clients.scopes import scoped_read
        #   return scoped_read(ctx, "kaseya", "/assetmgmt/agents")
        # The same works for an owner-defined CUSTOM integration (use its id as the vendor;
        # only its configured read paths are reachable):
        #   return scoped_read(ctx, "my_custom_integration", "/v1/things")
        # or ctx.client("kaseya").get(path). Return JSON-serializable data, or {"error": "..."}.
        #
        # A CATEGORY="write" tool changes vendor data via scoped_write (POST/PATCH only,
        # allow-listed path prefixes; there is no delete):
        #   from execution.clients.scopes import scoped_write
        #   return scoped_write(ctx, "m365", "/users", body={...}, method="POST")
        #
        # EXCHANGE ONLINE is NOT a scoped_read/scoped_write path vendor — it is a cmdlet connector
        # reached via ctx.client("exo").invoke("<Cmdlet>", {params}). ONLY these cmdlets exist
        # (anything else is refused): Get-Mailbox, Get-MailboxPermission, Get-RecipientPermission,
        # Get-AcceptedDomain (reads); New-Mailbox, Add-MailboxPermission, Add-RecipientPermission
        # (writes) PLUS any cmdlet the owner has added via propose_connector_capability. The full
        # mailbox-admin suite (Set-Mailbox, Remove-MailboxPermission, retention, groups, …) is
        # already hand-written — prefer an existing skill. If you need a cmdlet the connector still
        # refuses, do NOT fake it: the agent should call propose_connector_capability FIRST (the
        # owner approves the cmdlet), then this tool can use it. Mailbox DELETION is never available
        # to generated tools. Do NOT invent /mailbox/* REST paths (blocked). Example:
        #   return ctx.client("exo").invoke("Get-Mailbox", {"Identity": ident})
        ...

Rules: no imports except typing/json/re/math/datetime and execution.clients.scopes.
No file/network/os/subprocess access. No code that runs at import time (only the constants and
the run function). Read tools must be READ-ONLY (no scoped_write). Write tools must declare
CATEGORY = "write" and REQUIRES_APPROVAL = True, and validate their inputs.

CRITICAL — never report a success you didn't get: a connector call (scoped_read / scoped_write /
ctx.client(...).invoke / .get) returns its failure as a VALUE, e.g. {"error": "..."} or a
"blocked" message — it does NOT raise. run() MUST inspect every connector result and, if it
failed, return {"error": "..."} at the TOP LEVEL. Never return status "executed"/"done" (or bury
the error in a nested field) when the underlying call was blocked or errored. If you cannot do the
request with the cmdlets/paths above, return {"error": "<why>"} — do not fabricate a path.

Build a tool for this request:
"""


def _candidate_path(name: str) -> Path:
    safe = re.sub(r"[^a-z0-9_]", "", name.lower())
    return CANDIDATES_DIR / f"{safe}.py"


def _extract_code(text: str) -> str:
    """Strip markdown fences if the model added them."""
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def validate_candidate(code: str) -> dict[str, Any]:
    """Static safety + schema check. Returns {ok, issues, meta:{name,category,...}}."""
    issues: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"ok": False, "issues": [f"syntax error: {e}"], "meta": {}}

    consts: dict[str, Any] = {}
    has_run = False

    # top-level: only imports / assignments / the run def / docstring allowed (no import-time exec)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            pass                                  # roots are checked tree-wide below
        elif isinstance(node, ast.FunctionDef):
            if node.name == "run":
                has_run = True
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    try:
                        consts[t.id] = ast.literal_eval(node.value)
                    except Exception:
                        consts[t.id] = "<expr>"
        elif isinstance(node, ast.AnnAssign):
            pass
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            pass  # module docstring
        else:
            issues.append(f"disallowed top-level statement: {type(node).__name__} "
                          f"(no code may run at import time)")

    # forbidden names / dunder access / imports ANYWHERE in the tree — an `import os` nested
    # inside run() is exactly as dangerous as a top-level one (hole found via a real draft, D-40)
    touches_write = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            roots = ([a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import)
                     else [(node.module or "").split(".")[0]])
            for r in roots:
                if r in _FORBIDDEN_IMPORT_ROOTS:
                    issues.append(f"forbidden import: {r}")
                elif r and r not in _ALLOWED_IMPORT_ROOTS:
                    issues.append(f"import not allow-listed: {r}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
            issues.append(f"forbidden call: {node.func.id}()")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            issues.append(f"forbidden dunder access: {node.attr}")
        if isinstance(node, ast.Name) and node.id in ("__builtins__", "__import__"):
            issues.append(f"forbidden name: {node.id}")
        if ((isinstance(node, ast.Name) and node.id == "scoped_write")
                or (isinstance(node, ast.Attribute) and node.attr in ("scoped_write", "post",
                                                                      "patch", "delete"))):
            touches_write = True
        if ((isinstance(node, ast.Name) and node.id in ("invoke_destructive", "write_destructive"))
                or (isinstance(node, ast.Attribute)
                    and node.attr in ("invoke_destructive", "write_destructive"))):
            issues.append("generated tools may never call destructive primitives "
                          "(invoke_destructive / write_destructive) — destructive capabilities "
                          "are hand-written only")

    # schema lint
    for a in _REQUIRED_ATTRS:
        if a not in consts:
            issues.append(f"missing required attribute: {a}")
    if not has_run:
        issues.append("missing run() function")
    category = str(consts.get("CATEGORY", "read")).lower()
    if category not in _ALLOWED_CATEGORIES:
        issues.append(f"CATEGORY '{category}' not allowed for generated tools "
                      f"(use read/alert/write — never destructive)")
    # D-40 floors: a write candidate must arrive approval-gated and disabled; a non-write
    # candidate may not reach for write primitives (no smuggling past the allow_write gate).
    if category == "write" and consts.get("REQUIRES_APPROVAL") is not True:
        issues.append("write tools must declare REQUIRES_APPROVAL = True")
    if consts.get("ENABLED_BY_DEFAULT") is True:
        issues.append("generated tools must not set ENABLED_BY_DEFAULT = True")
    if touches_write and category != "write":
        issues.append("code uses write primitives (scoped_write/post/patch) but CATEGORY "
                      f"is '{category}' — declare CATEGORY = \"write\"")
    params = consts.get("PARAMETERS")
    if isinstance(params, dict) and params.get("type") != "object":
        issues.append("PARAMETERS must be a JSON-Schema object")

    meta = {"name": consts.get("NAME"), "category": category,
            "description": consts.get("DESCRIPTION"), "source": consts.get("SOURCE")}
    return {"ok": not issues, "issues": issues, "meta": meta}


def best_draft_model(router) -> Optional[str]:
    """Pick the model to draft a tool with: writing a whole module is capability-sensitive and slow
    on a local 27B (the cause of the 'draft failed: timed out' the agent path hit), so PREFER the
    first available cloud model; fall back to local only if no cloud model is reachable."""
    try:
        for m in router.available_models():
            if not m.get("local") and m.get("available", True):
                return m["id"]
    except Exception:                              # noqa: BLE001 — never block drafting on this
        pass
    return None                                    # resolve(None) → the local model


def _resolve_draft_model(router, model_id: Optional[str]) -> Optional[str]:
    """Honor an explicit CLOUD selection (the model the user picked). A local model — or none —
    is upgraded to a cloud model when one is reachable, because writing a whole module on a local
    27B exceeds Ollama's chat timeout (D-53). Local is used only if no cloud model exists."""
    if model_id and not str(model_id).startswith("ollama:"):
        return model_id
    return best_draft_model(router) or model_id


def draft(description: str, router=None, model_id: Optional[str] = None) -> dict[str, Any]:
    """Generate a candidate tool from a description, validate it, and stage it. No execution.
    Drafts with the user's selected model (the agent's running model via propose_tool), upgrading a
    local model to cloud since local code-gen times out."""
    from .router import ModelRouter
    router = router or ModelRouter()
    model_id = _resolve_draft_model(router, model_id)
    provider, model = router.resolve(model_id)
    try:
        result = provider.chat([{"role": "user", "content": PROMPT + description}], [], model)
        code = _extract_code(result.content)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"draft failed: {e}", "validation": {"ok": False, "issues": []}}
    validation = validate_candidate(code)
    name = (validation["meta"].get("name") or "generated_tool")
    path = _candidate_path(name)
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return {"ok": True, "name": _candidate_path(name).stem, "code": code,
            "validation": validation, "provider": getattr(provider, "name", "?"), "model": model}


def list_candidates() -> list[dict[str, Any]]:
    if not CANDIDATES_DIR.exists():
        return []
    out = []
    for p in sorted(CANDIDATES_DIR.glob("*.py")):
        if p.name == "__init__.py":
            continue
        code = p.read_text(encoding="utf-8")
        out.append({"name": p.stem, "code": code, "validation": validate_candidate(code),
                    "created": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()})
    return out


def reject(name: str) -> bool:
    p = _candidate_path(name)
    if p.exists():
        p.unlink()
        return True
    return False


def promote(name: str) -> dict[str, Any]:
    """Re-validate and move a candidate into the live skills/ dir. Caller must re-discover()."""
    p = _candidate_path(name)
    if not p.exists():
        return {"ok": False, "error": "candidate not found"}
    code = p.read_text(encoding="utf-8")
    v = validate_candidate(code)
    if not v["ok"]:
        return {"ok": False, "error": "validation failed", "validation": v}
    dest = SKILLS_DIR / p.name
    if dest.exists():
        return {"ok": False, "error": f"a live tool named {p.stem} already exists"}
    dest.write_text(code, encoding="utf-8")
    p.unlink()
    try:
        rel = str(dest.relative_to(_PROJECT_ROOT))
    except ValueError:
        rel = str(dest)
    return {"ok": True, "promoted": p.stem, "path": rel}
