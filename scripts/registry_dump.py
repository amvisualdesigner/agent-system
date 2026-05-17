#!/usr/bin/env python3
"""Dump contract registry as JSON."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.contracts.skill_registry import SKILL_CONTRACTS


def dump():
    output = {}
    for (cid, version), contract in SKILL_CONTRACTS.items():
        key = f"{cid}@{version}"
        output[key] = {
            "contract_id": contract.contract_id,
            "version": contract.version,
            "input_schema": contract.input_schema,
            "ast_template": contract.ast_template,
            "renderer": contract.renderer,
        }

    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as f:
            json.dump(output, f, indent=2)
        print(f"Registry dumped to {sys.argv[1]}")
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    dump()
