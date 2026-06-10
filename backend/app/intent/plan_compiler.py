"""PlanCompiler — determinista, sin LLM, sin StructuralIndex.

Toma ConfirmedIntent + contrato → CompiledPlan (skill_ir + semantic_frame + intents).
El lifecycle (CREATE/MODIFY/DELETE) lo decide complete_structure() en ApplyEngine.
"""

from __future__ import annotations

import logging
from typing import Any

from app.intent.models import ConfirmedIntent, CompiledPlan, IntentAction
from app.contracts.skill_registry import SkillContract, list_contracts, get_contract
from app.graphir.intent import (
    Intent,
    make_intent_id,
    resolve_graphir_type_from_capability,
)

logger = logging.getLogger(__name__)


def _build_actions(
    actions: list[IntentAction],
    params: dict,
) -> list[dict]:
    """Build top-level actions list from confirmed actions.

    Zero-loss: cada IntentAction produce exactamente un entry en la lista.
    """
    result = []
    for action in actions:
        entry = {
            "verb": action.verb,
            "target_capability": action.target_capability,
            "params": dict(action.params),
            "confidence": action.confidence,
        }
        if action.instance_hint:
            entry["instance_hint"] = action.instance_hint
        result.append(entry)
    return result


def _build_intents(
    actions: list[IntentAction],
    interpretation_id: str,
) -> list[dict]:
    """Build intent list from actions."""
    intents = []
    for action in actions:
        cap = action.target_capability
        intent_id = make_intent_id(
            f"{action.verb} {cap}", cap,
            seed=interpretation_id,
        )
        intent = Intent(
            id=intent_id,
            capability=cap,
            params=dict(action.params),
            task_fragment=f"{action.verb} {cap}",
            weight=action.confidence,
            source="confirmed_intent",
        )
        intents.append(intent.to_dict())
    return intents


def compile_plan(confirmed: ConfirmedIntent) -> CompiledPlan:
    """Compile ConfirmedIntent into a CompiledPlan.

    Args:
        confirmed: ConfirmedIntent from the user (after confirm step).

    Returns:
        CompiledPlan with skill_ir, semantic_frame, and intents.

    Raises:
        ValueError: if contract_id not found or actions invalid.
    """
    contract = get_contract(confirmed.contract_id, confirmed.contract_version)
    if contract is None:
        raise ValueError(
            f"Contract '{confirmed.contract_id}' version {confirmed.contract_version} not found."
        )

    # Gate: blocked if no actions
    gate_blocked = len(confirmed.actions) == 0

    # 1. Build SkillIR from confirmed params + contract
    skill_ir = _build_skill_ir(confirmed, contract)

    # 2. Build semantic_frame from confirmed actions.
    #    Container expansion is handled later by complete_structure() (3E composition sync)
    #    which promotes children to INSTANCE without regenerating their files.
    semantic_frame = _build_semantic_frame_from_actions(confirmed.actions, confirmed.params)

    # 3. Build intents from confirmed actions
    intents = _build_intents(confirmed.actions, confirmed.interpretation_id)

    # 4. Build top-level actions list (zero-loss: must match semantic_frame.actions)
    plan_actions = _build_actions(confirmed.actions, confirmed.params)
    semantic_action_count = len(semantic_frame.get("actions", []))
    if semantic_action_count > 0 and len(plan_actions) == 0:
        raise ValueError(
            f"Zero-loss invariant violated: {semantic_action_count} semantic actions "
            f"but 0 plan actions. Actions MUST flow through."
        )

    return CompiledPlan(
        skill_ir=skill_ir,
        semantic_frame=semantic_frame,
        intents=intents,
        contract_id=confirmed.contract_id,
        actions=plan_actions,
    )


def _build_skill_ir(confirmed: ConfirmedIntent, contract: SkillContract) -> dict:
    """Build SkillIR dict from confirmed intent + contract.

    Uses confirmed.params as base, fills contract defaults for missing params.
    """
    params = dict(confirmed.params)
    schema = contract.input_schema
    properties = schema.get("properties", {})

    for key, prop in properties.items():
        if key not in params and "default" in prop:
            params[key] = prop["default"]

    return {
        "contract_id": confirmed.contract_id,
        "version": confirmed.contract_version,
        "params": params,
        "confidence": 1.0,
    }


def _build_semantic_frame_from_actions(
    actions: list[IntentAction],
    params: dict,
) -> dict:
    """Build semantic_frame dict from (possibly expanded) actions.

    Formato compatible con el que espera ApplyEngine -> complete_structure.
    """
    frame_actions = []
    objects = []
    constraints = []

    for action in actions:
        obj = action.target_capability.split(".")[-1] if "." in action.target_capability else action.target_capability
        frame_action = {
            "verb": action.verb,
            "object": obj,
            "direct_object": obj,
            "confidence": action.confidence,
        }
        if action.instance_hint:
            frame_action["instance_hint"] = action.instance_hint
        # Preserve source_capability for transform/swap/replace actions
        src = action.params.get("source_capability", action.params.get("source", ""))
        if src:
            frame_action["reference"] = src
        frame_actions.append(frame_action)
        objects.append({
            "type": obj,
            "confidence": action.confidence,
        })

    for key, value in params.items():
        if key != "metrics":
            continue
        constraints.append({
            "param": key,
            "value": value,
            "source": "user_explicit",
            "confidence": 1.0,
        })

    return {
        "actions": frame_actions,
        "objects": objects,
        "constraints": constraints,
        "confidence": 1.0,
        "missing_info": [],
    }
