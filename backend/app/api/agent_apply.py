from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.engine.apply_engine import apply_engine
from app.contracts.apply_request import ApplyRequest
from app.runtime.context import build_context
from app.executor.worktree_manager import create_worktree
from app.intent.models import RunPhase
from app.utils.run_id import validate_run_id

router = APIRouter()


def _auto_init_run_state(run_id: str, plan: dict) -> None:
    """Initialize run state from a legacy plan when no /agent/interpret was called.

    This provides backward compatibility for the orchestrator, which uses the
    legacy /agent/plan endpoint (no state machine). When the state machine flow
    is used (POST /agent/interpret → /confirm → /apply), run_state already
    exists and this function is not called.
    """
    from app.state.run_state import save_run_state, transition_phase
    from app.intent.models import IntentAction

    semantic_frame = plan.get("semantic_frame", {})
    actions = semantic_frame.get("actions", [])
    confirmed_actions = []
    for a in actions:
        # Legacy planner usa "object" en vez de "target_capability"
        target = a.get("target_capability") or a.get("object", "")
        confirmed_actions.append(IntentAction(
            verb=a.get("verb", "modify"),
            target_capability=target,
            params=a.get("params", {}),
            confidence=a.get("confidence", 1.0),
        ))
    actions_dicts = [asdict(a) for a in confirmed_actions]

    skill_ir = plan.get("skill_ir", {})
    confirmed_intent = {
        "contract_id": skill_ir.get("contract_id", ""),
        "contract_version": skill_ir.get("version", 1),
        "actions": actions_dicts,
        "params": skill_ir.get("params", {}),
        "user_message": "",
        "interpretation_id": run_id,
    }

    save_run_state(run_id, {
        "phase": RunPhase.INTERPRETING.value,
        "interpretation_draft": {
            "status": "ok",
            "contract_id": confirmed_intent["contract_id"],
            "proposed_actions": actions_dicts,
            "alternatives": [],
            "params_proposed": confirmed_intent["params"],
            "worktree_capabilities": [],
        },
    })
    transition_phase(run_id, RunPhase.AWAITING_CONFIRMATION)
    save_run_state(run_id, {"confirmed_intent": confirmed_intent})
    transition_phase(run_id, RunPhase.CONFIRMED)


@router.post("/agent/apply")
def agent_apply(req: ApplyRequest):

    if not req.run_id:
        raise HTTPException(status_code=400, detail="run_id is required")
    run_id = validate_run_id(req.run_id)

    # ── State validation ──────────────────────────────────────────
    from app.state.run_state import load_run_state, transition_phase
    state = load_run_state(run_id)

    if state is None:
        if req.plan is None:
            raise HTTPException(
                status_code=400,
                detail="No run state and no plan provided. Call /agent/confirm first or pass a plan.",
            )
        # Backward-compatible path: orchestrator sending legacy plan without
        # prior /agent/interpret or /agent/confirm. Auto-init the run state.
        _auto_init_run_state(run_id, req.plan)
        state = load_run_state(run_id)

    current_phase = RunPhase(state.get("phase", RunPhase.INTERPRETING.value))
    if current_phase != RunPhase.CONFIRMED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot apply in phase '{current_phase.value}'. Expected 'confirmed'.",
        )

    confirmed_intent = state.get("confirmed_intent")
    if not confirmed_intent:
        raise HTTPException(status_code=400, detail="No confirmed intent found. Call /agent/confirm first.")

    if not confirmed_intent.get("actions"):
        raise HTTPException(status_code=400, detail="Confirmed intent has no actions. Nothing to execute.")

    gate = state.get("gate", {})
    if gate.get("blocked", False):
        raise HTTPException(status_code=400, detail=f"Gate blocked: {gate.get('reason', 'unknown')}")

    # Transition to applying
    transition_phase(run_id, RunPhase.APPLYING)

    context = build_context(run_id)
    context.workspace = create_worktree(run_id)

    # Use persisted plan when none passed (state machine flow)
    plan = req.plan or state.get("compiled_plan")

    try:
        result = apply_engine(run_id, plan, context, dry_run=req.dry_run)
        transition_phase(run_id, RunPhase.COMPLETED)
        return result
    except Exception as e:
        transition_phase(run_id, RunPhase.FAILED)
        raise HTTPException(status_code=500, detail=str(e))