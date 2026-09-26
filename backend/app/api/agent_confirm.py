from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.intent.models import ConfirmedIntent, IntentAction, RunPhase, PendingDeletion
from app.intent.plan_compiler import compile_plan
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
    page_context_choice: str | None = None


@router.post("/agent/confirm")
def agent_confirm(req: ConfirmRequest):
    try:
        return _agent_confirm(req)
    except Exception as e:
        logger.exception("confirm failed: %s", e)
        return {
            "status": "rejected",
            "reason": str(e),
            "gate": {"blocked": True, "reason": "confirm_exception"},
        }


def _agent_confirm(req: ConfirmRequest):
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

    # ── Clarification guard: do not confirm if interpretation still needs clarification ──
    draft = state.get("interpretation_draft", {})
    draft_status = draft.get("status") if isinstance(draft, dict) else None
    if draft_status == "needs_clarification":
        has_choices = bool(draft.get("choices"))
        has_page_choice = bool(req.page_context_choice)
        if has_choices and not has_page_choice:
            return {
                "status": "rejected",
                "reason": "Clarification required: choose a page context via page_context_choice.",
                "gate": {"blocked": True, "reason": "needs_clarification"},
            }
        if not has_choices:
            return {
                "status": "rejected",
                "reason": "Clarification required: rephrase your request first.",
                "gate": {"blocked": True, "reason": "needs_clarification"},
            }
        # If user provided page_context_choice, clarification is resolved — proceed

    # Validate contract exists
    contract = get_contract(req.contract_id, req.contract_version)
    if contract is None:
        return {
            "status": "rejected",
            "reason": f"Contract '{req.contract_id}' not found",
        }

    # Build ConfirmedIntent — only pass fields IntentAction accepts
    _ia_fields = {"verb", "target_capability", "params", "confidence", "instance_hint"}
    confirmed = ConfirmedIntent(
        contract_id=req.contract_id,
        contract_version=req.contract_version,
        actions=[IntentAction(**{k: v for k, v in a.items() if k in _ia_fields}) for a in req.actions],
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

    # Build preview summary from confirmed actions (no container expansion;
    # composition children are handled by complete_structure() at apply time)
    cap_labels = {}
    for cap_entry in (getattr(contract, '_catalog_labels', None) or {}):
        cap_labels[cap_entry["id"]] = cap_entry.get("label", cap_entry["id"])
    summary_parts = []
    for a in confirmed.actions:
        verb = a.verb
        cap = a.target_capability
        label = cap_labels.get(cap, cap.split(".")[-1])
        if verb and cap:
            summary_parts.append(f"{verb.capitalize()} {label}")
    summary = "; ".join(summary_parts) if summary_parts else "No changes"

    # Structural operations from confirmed actions
    structural_ops = []
    for a in confirmed.actions:
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

    # 3F: Estimate pipeline routes per operation (single materialization route).
    route_counts: dict[str, int] = {}
    for op in structural_ops:
        route = "delete_inject" if op["action"] == "DELETE" else "constraint"
        route_counts[route] = route_counts.get(route, 0) + 1

    # Phase 5B: Derive pending_deletions from confirmed actions
    pending_deletions = [
        PendingDeletion(capability=a.target_capability, instance_hint=a.instance_hint)
        for a in confirmed.actions
        if a.verb.lower() in ("remove", "delete")
    ]

    plan_preview = {
        "summary_human": summary,
        "structural_operations": structural_ops,
        "estimated_files": estimated_files,
        "routes": route_counts.copy(),
        "pending_deletions": [
            {"capability": pd.capability, "instance_hint": pd.instance_hint}
            for pd in pending_deletions
        ],
    }

    # ── PageCreator: if user chose create_new, generate page ops ──
    page_creator_ops: list[dict] = []
    forced_anchor_path: str | None = None
    if req.page_context_choice == "create_new":
        try:
            from app.engine.page_creator import (
                PageCreateTargetExistsError,
                create_page_ops,
            )
            from app.engine.page_context_resolver import extract_requested_context
            from app.runtime.context import build_context
            context = extract_requested_context(req.user_message)
            if context:
                ctx = build_context(run_id)
                ops = create_page_ops(context, ctx.workspace)
                page_creator_ops = [op.to_dict() for op in ops]
                # Extract the page path from the CREATE op for forced anchoring
                page_create_op = next((op for op in ops if op.action == "CREATE"), None)
                if page_create_op:
                    forced_anchor_path = page_create_op.path
                plan_preview["page_creator"] = {
                    "ops": [op.to_dict() for op in ops],
                    "summary": "Crear nueva página + ruta en router",
                }
                plan_preview["estimated_files"] = sorted(set(
                    list(plan_preview["estimated_files"]) + [op.path for op in ops]
                ))
        except PageCreateTargetExistsError as e:
            return {
                "status": "rejected",
                "reason": str(e),
                "gate": {"blocked": True, "reason": "page_target_exists"},
            }
        except Exception as e:
            logger.warning("PageCreator failed: %s", e)
            plan_preview["page_creator"] = {"ops": [], "summary": "Error al generar página"}

    result = {
        "status": "ok",
        "plan": plan.to_dict(),
        "plan_preview": plan_preview,
        "gate": gate,
    }

    # Persist: store confirmed intent, compiled plan, preview; transition to confirmed
    save_extra: dict = {
        "confirmed_intent": confirmed.to_dict(),
        "compiled_plan": plan.to_dict(),
        "plan_preview": plan_preview,
        "gate": gate,
    }
    if page_creator_ops:
        save_extra["page_creator_ops"] = page_creator_ops
    if forced_anchor_path:
        save_extra["forced_anchor_path"] = forced_anchor_path
    save_run_state(run_id, save_extra)
    transition_phase(run_id, RunPhase.CONFIRMED)

    return result
