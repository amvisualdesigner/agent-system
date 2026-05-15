import time
from state import AgentState
from backend_client import call_plan as backend_call_plan
from sse import emitter
from models import SSEEvent


async def call_plan_node(state: AgentState) -> dict:
    if state.get("cancelled"):
        return {"phase": "cancelled"}

    phase = "planning"
    state["phase"] = phase

    await emitter.emit(
        state["run_id"] or "",
        SSEEvent(type="node_start", node="call_plan", phase=phase, run_id=state.get("run_id")),
    )

    input_data = {"task": state["task"]}

    await emitter.emit(
        state["run_id"] or "",
        SSEEvent(
            type="tool_call", node="call_plan", phase=phase,
            run_id=state.get("run_id"),
            data={"tool": "/agent/plan", "input": input_data},
        ),
    )

    start = time.time()
    try:
        result = await backend_call_plan(state["task"])
        latency = int((time.time() - start) * 1000)
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        error_state = _error_result(state, "call_plan", str(e), input_data, latency)
        return error_state

    output = {"backend_run_id": result.get("run_id"), "plan": result.get("plan")}
    trace_entry = {"node": "call_plan", "input": input_data, "output": output, "latency_ms": latency}
    trace = (state.get("trace") or []) + [trace_entry]

    await emitter.emit(
        state["run_id"] or "",
        SSEEvent(
            type="node_end", node="call_plan", phase=phase,
            run_id=state["run_id"],
            data=output,
        ),
    )

    if result.get("status") == "rejected":
        return {
            "backend_run_id": result.get("run_id"),
            "plan": result.get("plan"),
            "error": result.get("reason", "plan_rejected"),
            "trace": trace[-50:],
            "phase": "error",
        }

    return {
        "backend_run_id": result.get("run_id"),
        "plan": result.get("plan"),
        "trace": trace[-50:],
        "phase": phase,
    }


def _error_result(state, node, error, input_data, latency):
    trace_entry = {"node": node, "input": input_data, "output": {"error": error}, "latency_ms": latency}
    trace = (state.get("trace") or []) + [trace_entry]
    return {
        "error": error,
        "trace": trace[-50:],
        "phase": "error",
    }
