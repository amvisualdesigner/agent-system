from fastapi import APIRouter, HTTPException

from app.engine.apply_engine import apply_engine
from app.contracts.apply_request import ApplyRequest
from app.runtime.context import build_context
from app.executor.worktree_manager import ensure_worktree
from app.intent.models import RunPhase
from app.utils.run_id import validate_run_id

router = APIRouter()


@router.post("/agent/apply")
def agent_apply(req: ApplyRequest):

    if not req.run_id:
        raise HTTPException(status_code=400, detail="run_id is required")
    run_id = validate_run_id(req.run_id)

    # ---- State validation -----------------------------------------
    from app.state.run_state import load_run_state, transition_phase
    state = load_run_state(run_id)

    if state is None:
        raise HTTPException(
            status_code=400,
            detail="No run state found. Call /agent/interpret and /agent/confirm first.",
        )

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
    ensure_worktree(context)

    # Use persisted plan when none passed (state machine flow)
    plan = req.plan or state.get("compiled_plan")

    try:
        result = apply_engine(run_id, plan, context, dry_run=req.dry_run)
        transition_phase(run_id, RunPhase.COMPLETED)
        return result
    except Exception as e:
        transition_phase(run_id, RunPhase.FAILED)
        raise HTTPException(status_code=500, detail=str(e))