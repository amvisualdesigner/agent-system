#!/usr/bin/env python3
"""Diff between two versions of the same contract."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.contracts.skill_registry import SKILL_CONTRACTS


def diff_contract(cid: str, v1: int, v2: int):
    key1 = (cid, v1)
    key2 = (cid, v2)

    c1 = SKILL_CONTRACTS.get(key1)
    c2 = SKILL_CONTRACTS.get(key2)

    if not c1:
        print(f"Contract {cid} version {v1} not found")
        sys.exit(1)
    if not c2:
        print(f"Contract {cid} version {v2} not found")
        sys.exit(1)

    changes = []

    if c1.input_schema != c2.input_schema:
        old_props = set(c1.input_schema.get("properties", {}).keys())
        new_props = set(c2.input_schema.get("properties", {}).keys())
        added = new_props - old_props
        removed = old_props - new_props
        if added:
            changes.append(f"  input_schema: added properties {added}")
        if removed:
            changes.append(f"  input_schema: removed properties {removed}")

    if c1.ast_template != c2.ast_template:
        changes.append("  ast_template: CHANGED (WARNING: should be new contract_id)")

    if c1.renderer != c2.renderer:
        changes.append("  renderer: changed")

    if changes:
        print(f"Diff {cid} @{v1} -> @{v2}:")
        for c in changes:
            print(c)
    else:
        print(f"No changes between {cid} @{v1} and @{v2}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: contract_diff.py <contract_id> <version_old> <version_new>")
        sys.exit(1)
    diff_contract(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
