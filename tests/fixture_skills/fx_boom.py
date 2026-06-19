NAME = "fx_boom"
DESCRIPTION = "raises, to test the loop never crashes"
SOURCE = "fixture"
CATEGORY = "read"
PARAMETERS = {"type": "object", "properties": {}, "additionalProperties": False}


def run(ctx, **_):
    raise RuntimeError("kaboom")
