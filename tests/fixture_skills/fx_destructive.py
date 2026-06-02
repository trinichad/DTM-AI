NAME = "fx_destructive"
DESCRIPTION = "destructive fixture — approval floor must always apply"
SOURCE = "fixture"
CATEGORY = "destructive"
RISK_LEVEL = "high"
ENABLED_BY_DEFAULT = True
PARAMETERS = {"type": "object", "properties": {}, "additionalProperties": False}

EXECUTED = {"value": False}


def run(ctx, **_):
    EXECUTED["value"] = True
    return {"did": "destroy"}
