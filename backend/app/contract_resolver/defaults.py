"""Pure default resolution and extra-field validation.

Two separate concerns:
1. apply_schema_defaults — fill missing params from schema defaults
2. validate_no_extra_fields — reject unknown fields
"""


def apply_schema_defaults(params: dict, schema: dict) -> dict:
    result = dict(params)
    for field, prop in schema.get("properties", {}).items():
        if field not in result and "default" in prop:
            result[field] = prop["default"]
    return result


def validate_no_extra_fields(params: dict, schema: dict) -> tuple[bool, str]:
    allowed = set(schema.get("properties", {}).keys())
    extra = set(params.keys()) - allowed
    if extra:
        return False, f"unknown_fields:{','.join(sorted(extra))}"
    return True, "ok"
