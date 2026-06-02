NAME = "fx_crosstenant"
DESCRIPTION = "tries to act on another tenant"
SOURCE = "fixture"
CATEGORY = "read"
PARAMETERS = {
    "type": "object",
    "properties": {"target": {"type": "string"}},
    "required": ["target"],
    "additionalProperties": False,
}


def run(ctx, target, **_):
    ctx.require_tenant(target)  # raises CrossTenantError when target != bound tenant
    return {"ok": True}
