NAME = "fx_write"
DESCRIPTION = "write fixture (must be gated)"
SOURCE = "fixture"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = True  # enabled, to prove the gate (not the enable flag) blocks it
PARAMETERS = {"type": "object", "properties": {}, "additionalProperties": False}

# Module-level marker flipped only if run() actually executes — lets tests assert it never did.
EXECUTED = {"value": False}


def run(ctx, **_):
    EXECUTED["value"] = True
    return {"did": "write"}
