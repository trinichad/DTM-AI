NAME = "fx_read"
DESCRIPTION = "read fixture"
SOURCE = "fixture"
CATEGORY = "read"
PARAMETERS = {
    "type": "object",
    "properties": {"x": {"type": "string"}},
    "required": ["x"],
    "additionalProperties": False,
}


def run(ctx, x, **_):
    return {"echo": x, "tenant": ctx.tenant_id}
