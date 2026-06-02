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
_ALLOWED_CATEGORIES = ("read", "alert")  # generated tools may not be write/destructive

PROMPT = """You write a single Python module for the DTM AI skill registry. Output ONLY the code
(no prose, no markdown fences). Follow this exact contract:

    \"\"\"<one-line docstring>\"\"\"
    from __future__ import annotations
    from typing import Any

    NAME = "<snake_case_unique_name>"
    DESCRIPTION = "<one line shown to the model>"
    SOURCE = "<kaseya|cylance|huntress|dtm_ai>"
    CATEGORY = "read"            # MUST be "read" or "alert" (never write/destructive)
    RISK_LEVEL = "low"
    ENABLED_BY_DEFAULT = False   # generated tools start disabled
    PARAMETERS = {"type": "object", "properties": {...}, "additionalProperties": False}

    def run(ctx, **kwargs):
        # READ-ONLY. Get vendor data via the scoped connectors:
        #   from execution.clients.scopes import scoped_read
        #   return scoped_read(ctx, "kaseya", "/assetmgmt/agents")
        # or ctx.client("kaseya").get(path). Return JSON-serializable data, or {"error": "..."}.
        ...

Rules: no imports except typing/json/re/math/datetime and execution.clients.scopes.
No file/network/os/subprocess access. No code that runs at import time (only the constants and
the run function). The tool must be READ-ONLY.

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
            roots = ([a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import)
                     else [(node.module or "").split(".")[0]])
            for r in roots:
                if r in _FORBIDDEN_IMPORT_ROOTS:
                    issues.append(f"forbidden import: {r}")
                elif r and r not in _ALLOWED_IMPORT_ROOTS:
                    issues.append(f"import not allow-listed: {r}")
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

    # forbidden names / dunder access anywhere in the tree
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
            issues.append(f"forbidden call: {node.func.id}()")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            issues.append(f"forbidden dunder access: {node.attr}")
        if isinstance(node, ast.Name) and node.id in ("__builtins__", "__import__"):
            issues.append(f"forbidden name: {node.id}")

    # schema lint
    for a in _REQUIRED_ATTRS:
        if a not in consts:
            issues.append(f"missing required attribute: {a}")
    if not has_run:
        issues.append("missing run() function")
    category = str(consts.get("CATEGORY", "read")).lower()
    if category not in _ALLOWED_CATEGORIES:
        issues.append(f"CATEGORY '{category}' not allowed for generated tools (use read/alert)")
    params = consts.get("PARAMETERS")
    if isinstance(params, dict) and params.get("type") != "object":
        issues.append("PARAMETERS must be a JSON-Schema object")

    meta = {"name": consts.get("NAME"), "category": category,
            "description": consts.get("DESCRIPTION"), "source": consts.get("SOURCE")}
    return {"ok": not issues, "issues": issues, "meta": meta}


def draft(description: str, router=None, model_id: Optional[str] = None) -> dict[str, Any]:
    """Generate a candidate tool from a description, validate it, and stage it. No execution."""
    from .router import ModelRouter
    router = router or ModelRouter()
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
