import logging
from state import AgentState
from sse import emitter
from models import SSEEvent, RunResult
from store import save_snapshot

logger = logging.getLogger("orchestrator.nodes.return_result")


async def return_result_node(state: AgentState) -> dict:
    assert state.get("run_id"), "run_id must be set and non-empty"
    run_id = state["run_id"]
    logger.info("node=return_result run_id=%s", run_id)

    error = state.get("error")
    cancelled = state.get("cancelled", False)
    phase = state.get("phase", "completed")

    if cancelled:
        logger.warning("[run_id=%s] result: cancelled", run_id)
        result = RunResult(run_id=run_id, status="cancelled")
    elif error:
        logger.warning("[run_id=%s] result: error=%s", run_id, error)
        result = RunResult(run_id=run_id, status="failed", error=error)
    elif phase == "awaiting_confirmation":
        logger.info("[run_id=%s] result: awaiting_confirmation", run_id)
        result = RunResult(run_id=run_id, status="awaiting_confirmation")
    elif phase == "confirmed":
        # Confirmed Plan parked — Apply has NOT run. `completed` is reserved
        # for a finished Apply; the backend authority here is still `confirmed`.
        # No execution block is invented: there is nothing executed yet.
        logger.info("[run_id=%s] result: confirmed (awaiting apply)", run_id)
        result = RunResult(
            run_id=run_id,
            plan=state.get("plan"),
            status="confirmed",
        )
    else:
        result_data = state.get("execution") or {}
        exec_block = result_data.get("execution", {})
        context_block = result_data.get("context", {})
        meta_block = result_data.get("meta")
        plan = state.get("plan")

        exec_status = exec_block.get("status", "ok")
        status = exec_status

        has_diff = bool(exec_block.get("diff"))
        has_snapshot = bool(context_block.get("repo_snapshot"))
        logger.info(
            "[run_id=%s] result: ok has_diff=%s has_snapshot=%s",
            run_id, has_diff, has_snapshot,
        )

        result = RunResult(
            run_id=run_id,
            plan=plan,
            execution=exec_block,
            context=context_block,
            meta=meta_block,
            status=status,
        )

    result_types = {"ok", "no_changes", "verify_failed", "conflict",
                    "awaiting_confirmation", "confirmed", "cancelled", "failed"}
    event_type = "result" if result.status in result_types else "error"

    # The backend RunPhase is the only lifecycle authority: the orchestrator
    # mirrors it. A conflict is NOT terminal (D1) — the Run goes back to
    # `confirmed` so an explicit retry is possible.
    if cancelled:
        phase_label = "cancelled"
    elif result.status == "conflict":
        phase_label = "confirmed"
    elif result.status == "confirmed":
        phase_label = "confirmed"
    elif result.status in ("ok", "no_changes"):
        phase_label = "completed"
    elif error or result.status in ("failed", "verify_failed", "rejected", "error"):
        phase_label = "failed"
    else:
        phase_label = phase

    snapshot = {
        "run_id": run_id,
        "task": state.get("task"),
        "session_id": state.get("session_id"),
        "phase": phase_label,
        "status": result.status,
        "plan": result.plan,
        "execution": result.execution,
        "context": result.context,
        "meta": result.meta,
        "trace": state.get("trace"),
        "error": result.error,
        "planner_meta": state.get("planner_meta"),
        # UI-3: persist for resume endpoints
        "interpretation": state.get("interpretation"),
        "confirmed_intent": state.get("confirmed_intent"),
        "plan_preview": state.get("plan_preview"),
        "gate": state.get("gate"),
        "plan_retryable": (result.execution or {}).get("plan_retryable", False),
    }
    try:
        save_snapshot(run_id, snapshot)
    except Exception as e:
        logger.error("[run_id=%s] failed to persist snapshot: %s", run_id, str(e))

    await emitter.emit(
        run_id,
        SSEEvent(
            type=event_type, node="return_result", phase=phase_label,
            run_id=run_id,
            data=result.model_dump() if hasattr(result, "model_dump") else result,
            error=result.error,
        ),
    )

    return {**state, "phase": phase_label}
