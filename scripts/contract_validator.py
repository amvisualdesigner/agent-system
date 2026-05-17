#!/usr/bin/env python3
"""Validate contract schema + AST consistency."""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.contracts.skill_registry import SKILL_CONTRACTS


def validate():
    errors = []
    for (cid, version), contract in SKILL_CONTRACTS.items():
        # contract_id must match key
        if contract.contract_id != cid:
            errors.append(f"{cid}@{version}: contract_id mismatch ({contract.contract_id})")

        # version must match key
        if contract.version != version:
            errors.append(f"{cid}@{version}: version mismatch ({contract.version})")

        # input_schema must be valid JSON Schema
        schema = contract.input_schema
        if "type" not in schema or schema["type"] != "object":
            errors.append(f"{cid}@{version}: input_schema missing type=object")
        if "properties" not in schema:
            errors.append(f"{cid}@{version}: input_schema missing properties")

        # ast_template must exist
        if not contract.ast_template:
            errors.append(f"{cid}@{version}: ast_template is empty")

        # renderer must have base_path and files
        renderer = contract.renderer
        if "base_path" not in renderer:
            errors.append(f"{cid}@{version}: renderer missing base_path")
        if "files" not in renderer or not renderer["files"]:
            errors.append(f"{cid}@{version}: renderer missing files")
        else:
            for f in renderer["files"]:
                if "path" not in f or "template" not in f:
                    errors.append(f"{cid}@{version}: file entry missing path or template: {f}")

    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print(f"OK: {len(SKILL_CONTRACTS)} contracts valid")
        sys.exit(0)


if __name__ == "__main__":
    validate()
