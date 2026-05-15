import time
from state import AgentState
from backend_client import call_apply as backend_call_apply, get_run as backend_get_run
from sse import emitter
from models import SSEEvent


async def call_apply_node(state: AgentState) -> dict:
    run_id = state.get("run_id", "")
    backend_run_id = state.get("backend_run_id", "")

    if state.get("cancelled"):
        return {"phase": "cancelled"}

    if state.get("error"):
        return {"phase": "error"}

    if state.get("execution") is not None:
        return {"phase": state.get("phase", "completed")}

    phase = "executing"
    plan = state.get("plan")

    await emitter.emit(
        run_id,
        SSEEvent(type="node_start", node="call_apply", phase=phase, run_id=run_id),
    )

    input_data = {"backend_run_id": backend_run_id, "plan": plan}

    await emitter.emit(
        run_id,
        SSEEvent(
            type="tool_call", node="call_apply", phase=phase,
            run_id=run_id,
            data={"tool": "/agent/apply", "input": input_data},
        ),
    )

    start = time.time()
    try:
        apply_result = await backend_call_apply(backend_run_id or run_id, plan)
        latency = int((time.time() - start) * 1000)
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return _error_state(state, "call_apply", str(e), input_data, latency)

    output = {
        "execution_status": apply_result.get("status"),
        "operations": apply_result.get("operations") or apply_result.get("execution"),
    }

    run_details = None
    try:
        run_details = await backend_get_run(backend_run_id or run_id)
        output["diff"] = run_details.get("diff")
        output["files"] = run_details.get("files")
        output["has_commit"] = run_details.get("has_commit")
    except Exception:
        pass

    trace_entry = {"node": "call_apply", "input": input_data, "output": output, "latency_ms": latency}
    trace = (state.get("trace") or []) + [trace_entry]

    await emitter.emit(
        run_id,
        SSEEvent(type="node_end", node="call_apply", phase=phase, run_id=run_id, data=output),
    )

    return {
        "execution": apply_result,
        "run_details": run_details,
        "trace": trace[-50:],
        "phase": phase,
    }


def _error_state(state, node, error, input_data, latency):
    trace_entry = {"node": node, "input": input_data, "output": {"error": error}, "latency_ms": latency}
    trace = (state.get("trace") or []) + [trace_entry]
    return {
        "error": error,
        "trace": trace[-50:],
        "phase": "error",
    }
