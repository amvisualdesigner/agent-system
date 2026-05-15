from state import AgentState
from sse import emitter
from models import SSEEvent, RunResult


async def return_result_node(state: AgentState) -> dict:
    run_id = state.get("run_id", "")
    error = state.get("error")
    cancelled = state.get("cancelled", False)

    if cancelled:
        result = RunResult(run_id=run_id, status="cancelled")
    elif error:
        result = RunResult(run_id=run_id, status="error", error=error)
    else:
        run_details = state.get("run_details") or {}
        execution = state.get("execution")
        plan = state.get("plan")
        status = "ok"
        if execution and execution.get("status") == "rejected":
            status = "rejected"

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
