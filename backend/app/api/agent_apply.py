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
    from app.state.run_state import load_run_state, save_run_state, transition_phase
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

    # F1/D2: the Confirmed Plan persisted at /agent/confirm is the only
    # lifecycle authority. req.plan can never substitute or reinterpret it.
    plan = state.get("compiled_plan")

    # ── PageCreator: FileOps flow through the single materialization set ──
    # No early/late writes: page CREATE + router MODIFY are seeded into
    # apply_engine and written only by the single terminal apply.
    page_creator_ops_raw = state.get("page_creator_ops", [])
    forced_anchor_path: str | None = state.get("forced_anchor_path")

    page_creator_ops = None
    if page_creator_ops_raw:
        from app.graphir.utils import FileOp
        page_creator_ops = [
            FileOp(**op) if isinstance(op, dict) else op
            for op in page_creator_ops_raw
        ]

    # ── Concurrency validation pre-apply (option C) ──
    # If the workspace changed between /agent/confirm (snapshot) and now →
    # the confirmed plan/preview is stale: NO WRITE, zero FileOps. No merge,
    # no reinterpretation, no target re-selection.
    concurrency_conflicts: list[str] = []
    confirm_snapshot = state.get("apply_snapshot")
    if isinstance(confirm_snapshot, dict) and confirm_snapshot:
        from app.engine.worktree_snapshot import snapshot_worktree, diff_snapshots
        current_snapshot = snapshot_worktree(context.workspace)
        concurrency_conflicts = diff_snapshots(confirm_snapshot, current_snapshot)

    if concurrency_conflicts and not req.dry_run:
        save_run_state(run_id, {"phase": RunPhase.CONFIRMED.value})
        return {
            "execution": {
                "status": "conflict",
                "reason": "concurrency",
                "detail": (
                    "NO WRITE: the workspace changed after the plan was "
                    "confirmed. Changed paths: "
                    + ", ".join(concurrency_conflicts[:20])
                    + (
                        (" (+%d more)" % (len(concurrency_conflicts) - 20))
                        if len(concurrency_conflicts) > 20 else ""
                    )
                    + ". Re-confirm with the current state to generate a "
                    "fresh preview."
                ),
                "diff": None,
                "operations": [],
            },
            "context": {"repo_snapshot": []},
        }

    try:
        result = apply_engine(
            run_id, plan, context,
            dry_run=req.dry_run,
            confirmed_deletions=req.confirmed_deletions,
            forced_anchor_path=forced_anchor_path,
            page_creator_ops=page_creator_ops,
        )
    except Exception as e:
        transition_phase(run_id, RunPhase.FAILED)
        raise HTTPException(status_code=500, detail=str(e))

    result.setdefault("meta", {})["concurrency"] = {
        "status": "ok" if not concurrency_conflicts else "stale",
        "changed_paths": concurrency_conflicts[:20],
    }

    transition_phase(run_id, RunPhase.COMPLETED)
    return result
