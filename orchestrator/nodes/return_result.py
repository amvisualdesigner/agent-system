import logging
from state import AgentState
from sse import emitter
from models import SSEEvent, RunResult
from store import save_snapshot

logger = logging.getLogger("orchestrator.nodes.return_result")


async def return_result_node(state: AgentState) -> dict:
    run_id = state.get("run_id", "")
    error = state.get("error")
    cancelled = state.get("cancelled", False)

    if cancelled:
        logger.warning("[run_id=%s] result: cancelled", run_id)
        result = RunResult(run_id=run_id, status="cancelled")
    elif error:
        logger.warning("[run_id=%s] result: error=%s", run_id, error)
        result = RunResult(run_id=run_id, status="error", error=error)
    else:
        run_details = state.get("run_details") or {}
        execution = state.get("execution")
        plan = state.get("plan")
        status = "ok"
        if execution and execution.get("status") == "rejected":
            status = "rejected"

        has_diff = bool(run_details.get("diff"))
        has_files = bool(run_details.get("files"))
        logger.info(
            "[run_id=%s] result: ok has_diff=%s has_files=%s",
            run_id, has_diff, has_files,
        )

        result = RunResult(
            run_id=run_id,
            plan=plan,
            execution=execution,
            diff=run_details.get("diff"),
            files=run_details.get("files"),
            status=status,
        )

    phase = "completed" if result.status == "ok" else "error"
    if cancelled:
        phase = "cancelled"

    event_type = "result" if result.status == "ok" else "error"

    snapshot = {
        "run_id": run_id,
        "task": state.get("task"),
        "phase": phase,
        "status": result.status,
        "plan": result.plan,
        "execution": result.execution,
        "diff": result.diff,
        "files": result.files,
        "trace": state.get("trace"),
        "error": result.error,
        "planner_meta": state.get("planner_meta"),
    }
    try:
        save_snapshot(run_id, snapshot)
    except Exception as e:
        logger.error("[run_id=%s] failed to persist snapshot: %s", run_id, str(e))

    await emitter.emit(
        run_id,
        SSEEvent(
            type=event_type, node="return_result", phase=phase,
            run_id=run_id,
            data=result.model_dump() if hasattr(result, "model_dump") else result,
            error=result.error,
        ),
    )

    return {"phase": phase}
