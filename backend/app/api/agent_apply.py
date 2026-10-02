import logging
import traceback

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engine.apply_engine import apply_engine
from app.contracts.apply_request import ApplyRequest
from app.intent.models import RunPhase, conflict_result
from app.runtime.context import build_context
from app.executor.worktree_manager import ensure_worktree
from app.utils.run_id import validate_run_id

logger = logging.getLogger(__name__)

router = APIRouter()

_SUCCESS_STATUSES = ("ok", "no_changes")


class RetryRequest(BaseModel):
    run_id: str | None = None
    dry_run: bool = False
    confirmed_deletions: list[str] = []


class CancelRequest(BaseModel):
    run_id: str | None = None
    reason: str | None = None


def _require_state(run_id: str) -> dict:
    from app.state.run_state import load_run_state

    state = load_run_state(run_id)
    if state is None:
        raise HTTPException(
            status_code=400,
            detail="No run state found. Call /agent/interpret and /agent/confirm first.",
        )
    return state


def _session_gate(session_id: str | None, action: str) -> None:
    if not session_id:
        return
    from app.executor.session_manager import resolve_session
    from app.session.models import SessionStatus

    session_rec = resolve_session(session_id)
    if session_rec.status != SessionStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Session {session_id} is '{session_rec.status.value}'. "
                f"Only ACTIVE sessions accept run {action}."
            ),
        )


@router.post("/agent/apply")
def agent_apply(req: ApplyRequest):
    try:
        return _agent_apply(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("apply failed for run_id=%s: %s\n%s",
                      getattr(req, 'run_id', '?'), e, traceback.format_exc())
        _fail_run(getattr(req, "run_id", None))
        return {
            "status": "error",
            "detail": str(e),
            "phase": RunPhase.FAILED.value,
        }


@router.post("/agent/retry")
def agent_retry(req: RetryRequest):
    try:
        return _agent_retry(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("retry failed for run_id=%s: %s\n%s",
                      getattr(req, 'run_id', '?'), e, traceback.format_exc())
        _fail_run(getattr(req, "run_id", None))
        return {
            "status": "error",
            "detail": str(e),
            "phase": RunPhase.FAILED.value,
        }


def _fail_run(run_id: str | None) -> None:
    """Never leave a Run parked in `applying`.

    `applying` is not cancellable, so a crash between the transition and the
    settle would be a dead end with no retry and no cancel.
    """
    if not run_id:
        return
    from app.state.run_state import load_run_state, transition_phase

    try:
        state = load_run_state(validate_run_id(run_id)) or {}
        if RunPhase(state.get("phase", "")) == RunPhase.APPLYING:
            transition_phase(run_id, RunPhase.FAILED)
    except Exception as e:  # pragma: no cover - best effort only
        logger.warning("could not settle run_id=%s into failed: %s", run_id, e)


@router.post("/agent/cancel")
def agent_cancel(req: CancelRequest):
    try:
        return _agent_cancel(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("cancel failed for run_id=%s: %s\n%s",
                      getattr(req, 'run_id', '?'), e, traceback.format_exc())
        _fail_run(getattr(req, "run_id", None))
        return {
            "status": "error",
            "detail": str(e),
            "phase": RunPhase.FAILED.value,
        }


def _agent_cancel(req: CancelRequest):
    run_id = validate_run_id(req.run_id or "")
    state = _require_state(run_id)

    from app.state.run_state import transition_phase

    current = RunPhase(state.get("phase", RunPhase.INTERPRETING.value))
    cancellable = (
        RunPhase.INTERPRETING,
        RunPhase.AWAITING_CONFIRMATION,
        RunPhase.CONFIRMED,
    )
    if current not in cancellable:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot cancel a run in phase '{current.value}'. "
                f"Cancellable phases: "
                + ", ".join(p.value for p in cancellable) + "."
            ),
        )

    _session_gate(state.get("session_id"), "continuation")
    transition_phase(run_id, RunPhase.CANCELLED)
    return {
        "status": "cancelled",
        "run_phase": RunPhase.CANCELLED.value,
        "stage": "cancelled",
        "plan_confirmed": bool(state.get("compiled_plan")),
        "plan_retryable": False,
        "reason": req.reason or "cancelled_by_user",
    }


def _execute_plan(
    run_id: str,
    state: dict,
    *,
    dry_run: bool,
    confirmed_deletions: list[str] | None,
    fresh_snapshot: bool,
) -> dict:
    """Apply the persisted Confirmed Plan. Shared by /apply and /retry.

    ``fresh_snapshot`` re-captures the physical baseline right before the
    apply (retry only). The WHAT/WHERE of the Plan is never touched.
    """
    from app.state.run_state import save_run_state, transition_phase

    session_id = state.get("session_id")

    transition_phase(run_id, RunPhase.APPLYING)

    context = build_context(run_id, session_id=session_id)
    ensure_worktree(context)

    # F1/D2: the Confirmed Plan persisted at /agent/confirm is the only
    # lifecycle authority. req.plan can never substitute or reinterpret it.
    plan = state.get("compiled_plan")

    page_creator_ops_raw = state.get("page_creator_ops", [])
    forced_anchor_path: str | None = state.get("forced_anchor_path")

    page_creator_ops = None
    if page_creator_ops_raw:
        from app.graphir.utils import FileOp
        page_creator_ops = [
            FileOp(**op) if isinstance(op, dict) else op
            for op in page_creator_ops_raw
        ]

    confirm_snapshot = state.get("apply_snapshot")
    if fresh_snapshot:
        from app.engine.worktree_snapshot import snapshot_worktree

        confirm_snapshot = snapshot_worktree(context.workspace)
        save_run_state(run_id, {"apply_snapshot": confirm_snapshot})

    def _refingerprint() -> list[str]:
        if not (isinstance(confirm_snapshot, dict) and confirm_snapshot):
            return []
        from app.engine.worktree_snapshot import snapshot_worktree, diff_snapshots
        current_snapshot = snapshot_worktree(context.workspace)
        return diff_snapshots(confirm_snapshot, current_snapshot)

    def _run_apply() -> dict:
        return apply_engine(
            run_id, plan, context,
            dry_run=dry_run,
            confirmed_deletions=confirmed_deletions,
            forced_anchor_path=forced_anchor_path,
            page_creator_ops=page_creator_ops,
        )

    # The physical baseline is validated for BOTH dry runs and real applies:
    # a pre-flight that ignores drift would promise an outcome the real apply
    # cannot deliver. A dry run writes nothing, so the check is read-only.
    from app.session.locks import keyed_run_scope, session_apply_lock

    scope = keyed_run_scope(session_id, run_id)
    with session_apply_lock(scope):
        concurrency_conflicts = _refingerprint()
        if concurrency_conflicts:
            result = conflict_result(
                "concurrency",
                "NO WRITE: the workspace changed after the plan was "
                "confirmed. Changed paths: "
                + ", ".join(concurrency_conflicts[:20])
                + (
                    (" (+%d more)" % (len(concurrency_conflicts) - 20))
                    if len(concurrency_conflicts) > 20 else ""
                )
                + ". Retry to re-capture the physical snapshot (the Confirmed "
                "Plan is unchanged).",
                details={"changed_paths": concurrency_conflicts[:20]},
            )
        else:
            try:
                result = _run_apply()
            except Exception as e:
                transition_phase(run_id, RunPhase.FAILED)
                raise HTTPException(status_code=500, detail=str(e))

    result.setdefault("meta", {})["concurrency"] = {
        "status": "ok" if not concurrency_conflicts else "stale",
        "changed_paths": concurrency_conflicts[:20],
    }
    return result


def _settle_phase(run_id: str, result: dict) -> None:
    """Map an apply/retry result to the run lifecycle.

    D1: a post-confirm conflict is NOT terminal — the Confirmed Plan stays
    authoritative and the Run returns to `confirmed` for an explicit retry.
    """
    from app.state.run_state import save_run_state, transition_phase

    execution_status = result.get("execution", {}).get("status")
    if execution_status == "conflict":
        execution = result.get("execution", {})
        transition_phase(run_id, RunPhase.CONFIRMED)
        save_run_state(run_id, {
            "last_conflict": {
                "conflict": execution.get("conflict"),
                "detail": execution.get("detail"),
                "candidates": execution.get("candidates", []),
            },
            "plan_retryable": bool(execution.get("plan_retryable", True)),
            "apply_result": execution_status,
        })
        return

    if execution_status in _SUCCESS_STATUSES:
        transition_phase(run_id, RunPhase.COMPLETED)
    else:
        transition_phase(run_id, RunPhase.FAILED)

    save_run_state(run_id, {
        "apply_result": execution_status,
        "plan_retryable": False,
        "last_conflict": None,
    })


def _validate_confirmed(state: dict, run_id: str) -> None:
    confirmed_intent = state.get("confirmed_intent")
    if not confirmed_intent:
        raise HTTPException(status_code=400, detail="No confirmed intent found. Call /agent/confirm first.")

    if not confirmed_intent.get("actions"):
        raise HTTPException(status_code=400, detail="Confirmed intent has no actions. Nothing to execute.")

    gate = state.get("gate", {})
    if gate.get("blocked", False):
        raise HTTPException(status_code=400, detail=f"Gate blocked: {gate.get('reason', 'unknown')}")


def _agent_apply(req: ApplyRequest):
    if not req.run_id:
        raise HTTPException(status_code=400, detail="run_id is required")
    run_id = validate_run_id(req.run_id)

    state = _require_state(run_id)

    current_phase = RunPhase(state.get("phase", RunPhase.INTERPRETING.value))
    if current_phase != RunPhase.CONFIRMED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot apply in phase '{current_phase.value}'. Expected 'confirmed'.",
        )

    _validate_confirmed(state, run_id)
    _session_gate(state.get("session_id"), "continuation")

    result = _execute_plan(
        run_id, state,
        dry_run=req.dry_run,
        confirmed_deletions=req.confirmed_deletions,
        fresh_snapshot=False,
    )
    _settle_phase(run_id, result)
    return result


def _agent_retry(req: RetryRequest):
    if not req.run_id:
        raise HTTPException(status_code=400, detail="run_id is required")
    run_id = validate_run_id(req.run_id)

    state = _require_state(run_id)

    current_phase = RunPhase(state.get("phase", RunPhase.INTERPRETING.value))
    if current_phase != RunPhase.CONFIRMED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot retry in phase '{current_phase.value}'. "
                "Retry requires a Run parked in 'confirmed' after a conflict."
            ),
        )

    if not state.get("compiled_plan"):
        raise HTTPException(status_code=400, detail="No Confirmed Plan to retry. Call /agent/confirm first.")

    if state.get("plan_retryable") is False:
        raise HTTPException(
            status_code=409,
            detail="The last conflict is not retryable. Start a new run to change WHAT.",
        )

    _validate_confirmed(state, run_id)
    _session_gate(state.get("session_id"), "continuation")

    result = _execute_plan(
        run_id, state,
        dry_run=req.dry_run,
        confirmed_deletions=req.confirmed_deletions,
        fresh_snapshot=True,
    )
    result.setdefault("meta", {})["retry"] = {"same_plan": True, "fresh_snapshot": True}
    _settle_phase(run_id, result)
    return result