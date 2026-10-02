"""Structured, deterministic instance selection (Fase 5A).

Cuando una capability tiene N>1 instancias físicas, la ambigüedad es de
instancia (no semántica): se ofrecen opciones estructuradas derivadas
únicamente de hechos del worktree (StructuralIndex). Sin LLM, sin fuzzy,
sin selección silenciosa.
"""
from __future__ import annotations

import os

from app.engine.structural_index import StructuralIndex


def _stem(path: str | None) -> str:
    if not path:
        return ""
    return os.path.splitext(os.path.basename(path))[0]


def _choice_for(inst) -> dict:
    hint = inst.slot_id or inst.instance_id
    return {
        "kind": "instance",
        "capability": inst.capability,
        "instance_id": inst.instance_id,
        "slot_id": inst.slot_id,
        "label": inst.slot_id or _stem(inst.file_path) or inst.path,
        "path": inst.path,
        "file_path": inst.file_path,
        "anchor": inst.anchor,
        "instance_hint": hint,
    }


def build_instance_choices(
    capability: str,
    structural_index: StructuralIndex | None,
) -> list[dict]:
    """Return structured choices for a capability with >1 physical instance."""
    if structural_index is None or not capability:
        return []
    instances = structural_index.get_instances(capability)
    if len(instances) <= 1:
        return []
    return [_choice_for(i) for i in instances]


def enrich_draft_instance_choices(
    draft_dict: dict,
    structural_index: StructuralIndex | None,
) -> None:
    """Attach `instance_choices` to ambiguous actions (mutates draft_dict)."""
    if structural_index is None:
        return
    for action in draft_dict.get("proposed_actions") or []:
        cap = action.get("target_capability") or ""
        if action.get("instance_hint"):
            continue
        choices = build_instance_choices(cap, structural_index)
        if choices:
            action["instance_choices"] = choices
    all_choices = [
        c
        for a in draft_dict.get("proposed_actions") or []
        for c in (a.get("instance_choices") or [])
    ]
    draft_dict["instance_choices"] = all_choices
    draft_dict["instance_ambiguous"] = bool(all_choices)
