import logging
import traceback

from fastapi import APIRouter, HTTPException

from app.engine.apply_engine import apply_engine
from app.contracts.apply_request import ApplyRequest
from app.runtime.context import build_context
from app.executor.worktree_manager import ensure_worktree
from app.intent.models import RunPhase
from app.utils.run_id import validate_run_id

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/agent/apply")
def agent_apply(req: ApplyRequest):
    try:
        return _agent_apply(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("apply failed for run_id=%s: %s\n%s",
                      getattr(req, 'run_id', '?'), e, traceback.format_exc())
        return {
            "status": "error",
            "detail": str(e),
            "phase": RunPhase.FAILED.value,
        }


def _agent_apply(req: ApplyRequest):
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

    # ── PageCreator pre-apply: create the page file BEFORE apply_engine ──
    # so the anchor resolver discovers it and can be forced to use it.
    page_creator_ops_raw = state.get("page_creator_ops", [])
    forced_anchor_path: str | None = state.get("forced_anchor_path")
    page_modify_ops_raw: list[dict] = []

    if page_creator_ops_raw and forced_anchor_path and not req.dry_run:
        from app.graphir.constraint.executor import FileOpApplier
        from app.graphir.utils import FileOp
        applier = FileOpApplier(context.workspace)
        ops = [FileOp(**op) if isinstance(op, dict) else op for op in page_creator_ops_raw]
        for fop in ops:
            if fop.action == "CREATE":
                applier.apply([fop])
                logger.info("Applied page_creator CREATE: %s", fop.path)
            elif fop.action == "MODIFY":
                page_modify_ops_raw.append(fop)
        # Rebuild context to pick up the new page file
        context = build_context(run_id)
        ensure_worktree(context)

    try:
        result = apply_engine(
            run_id, plan, context,
            dry_run=req.dry_run,
            confirmed_deletions=req.confirmed_deletions,
            forced_anchor_path=forced_anchor_path,
        )
    except Exception as e:
        transition_phase(run_id, RunPhase.FAILED)
        raise HTTPException(status_code=500, detail=str(e))

    # ── Apply page_creator MODIFY (router) ops AFTER apply_engine ──
    if page_modify_ops_raw and not req.dry_run:
        try:
            from app.graphir.constraint.executor import FileOpApplier
            from app.graphir.utils import FileOp
            applier = FileOpApplier(context.workspace)
            for fop in page_modify_ops_raw:
                applier.apply([fop])
            logger.info("Applied %d page_creator MODIFY ops for run_id=%s", len(page_modify_ops_raw), run_id)
            existing = result.get("fileops", [])
            result["fileops"] = existing + [op.to_dict() if hasattr(op, 'to_dict') else op for op in page_modify_ops_raw]
        except Exception as e:
            logger.warning("Failed to apply page_creator MODIFY ops: %s", e)
            result["page_creator_warning"] = str(e)

    # ── Backward compat: apply page_creator_ops after apply_engine
    #    when forced_anchor_path is not set (old confirm state) ──
    if page_creator_ops_raw and not forced_anchor_path and not req.dry_run:
        try:
            from app.graphir.constraint.executor import FileOpApplier
            from app.graphir.utils import FileOp
            applier = FileOpApplier(context.workspace)
            ops = [FileOp(**op) if isinstance(op, dict) else op for op in page_creator_ops_raw]
            for fop in ops:
                applier.apply([fop])
            logger.info("Applied %d page_creator_ops (backward compat) for run_id=%s", len(ops), run_id)
            existing = result.get("fileops", [])
            result["fileops"] = existing + [op.to_dict() if hasattr(op, 'to_dict') else op for op in ops]
        except Exception as e:
            logger.warning("Failed to apply page_creator_ops (backward compat): %s", e)
            result["page_creator_warning"] = str(e)

    if page_creator_ops_raw and req.dry_run:
        result["page_creator_ops"] = page_creator_ops_raw

    transition_phase(run_id, RunPhase.COMPLETED)
    return result
