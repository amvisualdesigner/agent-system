"""Build dynamic AST from contract ast_template + resolved params.

Fully materialized AST — no source references, no $ref, no implicit binding.
Each slot has explicit props resolved from params at build time.
"""

import copy

from app.contracts.skill_registry import SkillContract


def _resolve_props(props_template: dict, params: dict) -> dict:
    resolved = {}
    for prop_name, param_key in props_template.items():
        if param_key in params:
            resolved[prop_name] = params[param_key]
    return resolved


def build_ast(contract: SkillContract, params: dict) -> dict:
    template = contract.ast_template
    nodes = []
    for slot in template.get("slots", []):
        nodes.append({
            "type": slot["type"],
            "props": _resolve_props(slot.get("props", {}), params),
        })
    return {
        "layout": template["layout"],
        "nodes": nodes,
    }
