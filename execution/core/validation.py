"""Minimal JSON-Schema validator for tool PARAMETERS — zero dependency.

The full `jsonschema` library is overkill (and a wheel-availability risk on new
Python). Our tool PARAMETERS only ever use a small, well-defined subset:

  {"type": "object",
   "properties": { "<name>": {"type": "string|integer|number|boolean|array",
                              "enum": [...], "items": {...}} },
   "required": ["..."],
   "additionalProperties": false}

This validator covers exactly that subset and REJECTS anything it cannot prove
valid (fail-closed, Behavioral Rule #3). Tool args from the LLM are validated here
before run() is ever called.
"""
from __future__ import annotations

from typing import Any

_PYTYPE = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class SchemaError(ValueError):
    """Args did not satisfy the tool's PARAMETERS schema."""


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Validate `args` against `schema`. Returns the (coerced) args or raises SchemaError."""
    if not isinstance(schema, dict):
        raise SchemaError("PARAMETERS schema must be an object")
    if schema.get("type", "object") != "object":
        raise SchemaError("top-level PARAMETERS schema must be type 'object'")
    if not isinstance(args, dict):
        raise SchemaError("tool arguments must be an object")

    props: dict[str, Any] = schema.get("properties", {}) or {}
    required: list[str] = schema.get("required", []) or []
    additional = schema.get("additionalProperties", True)

    # Reject unknown keys when additionalProperties is false (default-deny posture).
    if additional is False:
        unknown = set(args) - set(props)
        if unknown:
            raise SchemaError(f"unexpected argument(s): {', '.join(sorted(unknown))}")

    # Required fields present.
    missing = [k for k in required if k not in args]
    if missing:
        raise SchemaError(f"missing required argument(s): {', '.join(missing)}")

    out: dict[str, Any] = {}
    for key, value in args.items():
        spec = props.get(key)
        if spec is None:
            out[key] = value  # only reachable when additionalProperties is not false
            continue
        out[key] = _validate_value(key, spec, value)
    return out


def _validate_value(key: str, spec: dict[str, Any], value: Any) -> Any:
    typ = spec.get("type")
    if typ is not None:
        expected = _PYTYPE.get(typ)
        if expected is None:
            raise SchemaError(f"{key}: unsupported type '{typ}' in schema")
        # bool is a subclass of int — guard against it sneaking into integer/number.
        if typ in ("integer", "number") and isinstance(value, bool):
            raise SchemaError(f"{key}: expected {typ}, got boolean")
        if not isinstance(value, expected):
            raise SchemaError(f"{key}: expected {typ}, got {type(value).__name__}")

    enum = spec.get("enum")
    if enum is not None and value not in enum:
        raise SchemaError(f"{key}: '{value}' not in allowed values {enum}")

    if typ == "array":
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            value = [_validate_value(f"{key}[]", item_spec, v) for v in value]

    return value
