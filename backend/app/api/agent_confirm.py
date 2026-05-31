from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.feature_flags import FEATURE_FLAGS
from app.intent.models import ConfirmedIntent, IntentAction, RunPhase
from app.intent.plan_compiler import compile_plan, expand_container_actions
from app.contracts.skill_registry import SkillContract, get_contract
from app.utils.run_id import validate_run_id

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_capability_file_map(contract: SkillContract) -> dict[str, str]:
    """Build mapping capability_id → file path from contract renderer + ast_template.

    Uses ast_template.capabilities (component_name → capability_id) to map
    each renderer file to the capability it implements.
    """
    renderer = contract.renderer or {}
    base_path = renderer.get("base_path", "")
    capabilities_map = contract.ast_template.get("capabilities", {})

    cap_to_file: dict[str, str] = {}
    for f_entry in renderer.get("files", []):
        fname = f_entry["path"].rsplit("/", 1)[-1].replace(".tsx", "")
        cap = capabilities_map.get(fname)
        if cap:
            cap_to_file[cap] = f"{base_path}{f_entry['path']}"

    return cap_to_file


class ConfirmRequest(BaseModel):
    run_id: str
    interpretation_id: str
    contract_id: str
    contract_version: int = 1
    actions: list[dict] = []
    params: dict = {}
    user_message: str = ""


@router.post("/agent/confirm")
def agent_confirm(req: ConfirmRequest):
    if not req.run_id:
        raise HTTPException(status_code=400, detail="run_id is required")
    if not req.contract_id:
        raise HTTPException(status_code=400, detail="contract_id is required")

    run_id = validate_run_id(req.run_id)

    # ── State validation ──────────────────────────────────────────
    from app.state.run_state import load_run_state, save_run_state, transition_phase
    state = load_run_state(run_id)

    # Test 2: confirm without prior interpret → must fail
    if state is None:
        return {
            "status": "rejected",
            "reason": "No interpretation draft found. Call /agent/interpret first.",
            "gate": {"blocked": True, "reason": "no_draft"},
        }

    current_phase = RunPhase(state.get("phase", RunPhase.INTERPRETING.value))
    if current_phase not in (RunPhase.AWAITING_CONFIRMATION, RunPhase.CONFIRMED):
        return {
            "status": "rejected",
            "reason": f"Cannot confirm in phase '{current_phase.value}'. Expected 'awaiting_confirmation' or 'confirmed'.",
            "gate": {"blocked": True, "reason": "wrong_phase"},
        }

    # Validate contract exists
    contract = get_contract(req.contract_id, req.contract_version)
    if contract is None:
        return {
            "status": "rejected",
            "reason": f"Contract '{req.contract_id}' not found",
        }

    # Build ConfirmedIntent
    confirmed = ConfirmedIntent(
        contract_id=req.contract_id,
        contract_version=req.contract_version,
        actions=[IntentAction(**a) for a in req.actions],
        params=req.params,
        user_message=req.user_message,
        interpretation_id=req.interpretation_id,
    )

    # Gate: reject if no actions
    if not confirmed.actions:
        return {
            "status": "rejected",
            "reason": "No actions to execute",
            "gate": {"blocked": True, "reason": "no_actions"},
        }

    # Compile plan
    try:
        plan = compile_plan(confirmed)
    except ValueError as e:
        return {
            "status": "rejected",
            "reason": str(e),
            "gate": {"blocked": True, "reason": str(e)},
        }

    # ── Idempotency: if already confirmed with same intent, return cached plan ──
    if current_phase == RunPhase.CONFIRMED and state.get("compiled_plan"):
        cached = {
            "status": "ok",
            "plan": state["compiled_plan"],
            "plan_preview": state.get("plan_preview"),
            "gate": state.get("gate", {"blocked": False}),
            "idempotent": True,
        }
        return cached

    # Container expansion for preview (cosmetic, no index needed)
    preview_actions = expand_container_actions(confirmed.actions, contract)

    # Build preview summary
    cap_labels = {}
    for cap_entry in (getattr(contract, '_catalog_labels', None) or {}):
        cap_labels[cap_entry["id"]] = cap_entry.get("label", cap_entry["id"])
    summary_parts = []
    for a in preview_actions:
        verb = a.verb
        cap = a.target_capability
        label = cap_labels.get(cap, cap.split(".")[-1])
        if verb and cap:
            summary_parts.append(f"{verb.capitalize()} {label}")
    summary = "; ".join(summary_parts) if summary_parts else "No changes"

    # Structural operations from expanded actions
    structural_ops = []
    for a in preview_actions:
        action_verb = a.verb.upper()
        if action_verb == "REMOVE":
            action_verb = "DELETE"
        elif action_verb not in ("CREATE", "MODIFY", "DELETE", "KEEP"):
            action_verb = a.verb.upper()
        structural_ops.append({
            "action": action_verb,
            "target": a.target_capability,
        })

    # 3D: Estimated files now derived from structural_ops (not all contract files)
    # Only include files for capabilities that will actually be touched.
    cap_to_file = _build_capability_file_map(contract)
    estimated_files = sorted(set(
        cap_to_file[op["target"]] for op in structural_ops
        if op["target"] in cap_to_file
    ))

    gate_blocked = False
    gate = {"blocked": gate_blocked, "reason": None}

    # 3F: Estimate pipeline routes per operation
    constraint_enabled = FEATURE_FLAGS.get("constraint_graph", False)
    route_counts: dict[str, int] = {}
    for op in structural_ops:
        route = "delete_inject" if op["action"] == "DELETE" else (
            "constraint" if constraint_enabled else "renderer"
        )
        route_counts[route] = route_counts.get(route, 0) + 1

    plan_preview = {
        "summary_human": summary,
        "structural_operations": structural_ops,
        "estimated_files": estimated_files,
        "routes": route_counts.copy(),
    }

    result = {
        "status": "ok",
        "plan": plan.to_dict(),
        "plan_preview": plan_preview,
        "gate": gate,
    }

    # Persist: store confirmed intent, compiled plan, preview; transition to confirmed
    save_run_state(run_id, {
        "confirmed_intent": confirmed.to_dict(),
        "compiled_plan": plan.to_dict(),
        "plan_preview": plan_preview,
        "gate": gate,
    })
    transition_phase(run_id, RunPhase.CONFIRMED)

    return result
