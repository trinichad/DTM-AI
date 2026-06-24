"""Fixture mirror of the real `bulk` meta-tool — NAME must be 'bulk' so dispatch() intercepts it
against the fixture registry (the real skills package isn't loaded in these tests)."""

NAME = "bulk"
DESCRIPTION = "run one tool many times (fixture)"
SOURCE = "msp_ai"
CATEGORY = "read"
ENABLED_BY_DEFAULT = True
PARAMETERS = {
    "type": "object",
    "properties": {
        "tool": {"type": "string"},
        "items": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["tool", "items"],
    "additionalProperties": False,
}


def run(ctx, **_):  # pragma: no cover - dispatch intercepts before run
    return {"ok": False, "error": "bulk handled by dispatch"}
