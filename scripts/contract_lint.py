#!/usr/bin/env python3
"""Lint contracts for common issues."""

import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.contracts.skill_registry import SKILL_CONTRACTS

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "app", "renderer", "templates")


def _fields_in_ast(slots: list, field: str) -> bool:
    """Check if a field appears as a slot source or in slot data."""
    for slot in slots:
        if slot.get("source") == field:
            return True
        for val in slot.values():
            if isinstance(val, str) and field in val:
                return True
    return False


def _max_slot_depth(slots: list, depth: int = 0) -> int:
    if not slots:
        return depth
    child_depths = []
    for slot in slots:
        children = slot.get("slots", [])
        if children:
            child_depths.append(_max_slot_depth(children, depth + 1))
        else:
            child_depths.append(depth + 1)
    return max(child_depths)


def lint():
    errors = []
    warnings = []
    for (cid, version), contract in SKILL_CONTRACTS.items():
        # --- contract_id should not have special chars ---
        if not cid.replace(".", "").replace("_", "").isalnum():
            errors.append(f"{cid}@{version}: contract_id has special characters")

        props = contract.input_schema.get("properties", {})
        required = contract.input_schema.get("required", [])

        # --- required fields must exist in properties ---
        for r in required:
            if r not in props:
                errors.append(f"{cid}@{version}: required field '{r}' not in properties")

        # --- enum values should be lowercase ---
        for pname, pschema in props.items():
            enum = pschema.get("enum", [])
            for val in enum:
                if val != val.lower():
                    errors.append(f"{cid}@{version}: enum value '{val}' in '{pname}' should be lowercase")

        # --- ast_template must exist with layout and slots ---
        if "layout" not in contract.ast_template:
            errors.append(f"{cid}@{version}: ast_template missing layout")
        if "slots" not in contract.ast_template:
            errors.append(f"{cid}@{version}: ast_template missing slots")

        # --- CONTRACT COMPLETENESS RULE ---
        # Every input_schema field must be used in AST OR have explicit default.
        # If optional + unused in AST + has default → warning (must be documented).
        for fname, pschema in props.items():
            is_used = _fields_in_ast(contract.ast_template.get("slots", []), fname)
            has_default = "default" in pschema
            if not is_used and not has_default:
                errors.append(
                    f"{cid}@{version}: field '{fname}' has no default and is not referenced in AST"
                )
            if not is_used and has_default:
                warnings.append(
                    f"{cid}@{version}: field '{fname}' is optional, has default, but is unused in AST — "
                    "must be explicitly documented in contract docs"
                )

        # --- 1 AST root per contract ---
        layouts = contract.ast_template.get("layout", "")
        if isinstance(layouts, list) and len(layouts) > 1:
            errors.append(f"{cid}@{version}: multiple AST roots ({len(layouts)} layouts)")

        # --- Max depth AST ≤ 3 ---
        slots = contract.ast_template.get("slots", [])
        depth = _max_slot_depth(slots)
        if depth > 3:
            errors.append(f"{cid}@{version}: AST depth {depth} exceeds max 3")

        # --- renderer template references ---
        renderer = contract.renderer
        for f in renderer.get("files", []):
            tmpl = f.get("template", "")
            if not tmpl.endswith(".j2"):
                errors.append(f"{cid}@{version}: template '{tmpl}' should use .j2 extension")

    if errors:
        print(f"LINT ISSUES ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    elif warnings:
        print(f"LINT OK with {len(warnings)} warnings:")
        for w in warnings:
            print(f"  - {w}")
        print(f"\n{len(SKILL_CONTRACTS)} contracts clean")
    else:
        print(f"LINT OK: {len(SKILL_CONTRACTS)} contracts clean")


if __name__ == "__main__":
    lint()
