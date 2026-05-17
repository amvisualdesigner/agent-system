#!/usr/bin/env python3
"""Detect semantically similar contracts (by name or schema)."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.contracts.skill_registry import SKILL_CONTRACTS


def _normalize(name: str) -> str:
    return name.lower().replace("_", "").replace("-", "").replace(".", "")


def detect():
    contracts = list(SKILL_CONTRACTS.keys())
    duplicates = []

    for i in range(len(contracts)):
        for j in range(i + 1, len(contracts)):
            a_id, a_ver = contracts[i]
            b_id, b_ver = contracts[j]

            # same contract_id, different version — skip
            if a_id == b_id:
                continue

            a_norm = _normalize(a_id)
            b_norm = _normalize(b_id)

            # exact match after normalization
            if a_norm == b_norm:
                duplicates.append((a_id, b_id, "normalized_name_exact"))
                continue

            # subname match
            if a_norm in b_norm or b_norm in a_norm:
                duplicates.append((a_id, b_id, "subname_overlap"))

            # same schema properties
            ca = SKILL_CONTRACTS[contracts[i]]
            cb = SKILL_CONTRACTS[contracts[j]]
            a_props = set(ca.input_schema.get("properties", {}).keys())
            b_props = set(cb.input_schema.get("properties", {}).keys())
            if a_props and b_props and a_props == b_props:
                duplicates.append((a_id, b_id, "identical_schema"))

    if duplicates:
        print(f"POTENTIAL DUPLICATES ({len(duplicates)}):")
        for a, b, reason in duplicates:
            print(f"  {a} <-> {b}  ({reason})")
    else:
        print("OK: no potential duplicates detected")


if __name__ == "__main__":
    detect()
