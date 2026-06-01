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
        result = RunResult(run_id=run_id, status="error", error=error)
    elif phase == "awaiting_confirmation":
        logger.info("[run_id=%s] result: awaiting_confirmation", run_id)
        result = RunResult(run_id=run_id, status="awaiting_confirmation")
    elif phase == "awaiting_apply":
        logger.info("[run_id=%s] result: awaiting_apply", run_id)
        result = RunResult(run_id=run_id, status="awaiting_apply")
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

    terminal_ok = {"ok", "clarification_needed", "verify_failed"}
    phase_label = "completed" if result.status in terminal_ok else phase
    if cancelled:
        phase_label = "cancelled"

    result_types = {"ok", "clarification_needed", "verify_failed", "awaiting_confirmation", "awaiting_apply"}
    event_type = "result" if result.status in result_types else "error"

    snapshot = {
        "run_id": run_id,
        "task": state.get("task"),
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
