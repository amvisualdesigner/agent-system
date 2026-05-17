"""Validate SkillIR params against contract input_schema."""

from typing import Any

from app.contracts.skill_registry import SkillContract


def validate_input(params: dict, contract: SkillContract) -> tuple[bool, str]:
    schema = contract.input_schema
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for field in required:
        if field not in params or params[field] is None:
            return False, f"missing_required_field:{field}"

    for field, value in params.items():
        if field not in properties:
            continue
        prop_schema = properties[field]
        enum = prop_schema.get("enum")
        if enum is not None and value not in enum:
            return False, f"invalid_enum:{field}={value}"

        prop_type = prop_schema.get("type", "string")
        if prop_type == "array":
            if not isinstance(value, list):
                return False, f"expected_array:{field}"
            items_schema = prop_schema.get("items", {})
            item_enum = items_schema.get("enum")
            if item_enum:
                for item in value:
                    if item not in item_enum:
                        return False, f"invalid_enum_item:{field}={item}"
            min_items = prop_schema.get("minItems", 0)
            max_items = prop_schema.get("maxItems")
            if len(value) < min_items:
                return False, f"min_items_not_met:{field}"
            if max_items is not None and len(value) > max_items:
                return False, f"max_items_exceeded:{field}"

    return True, "ok"
