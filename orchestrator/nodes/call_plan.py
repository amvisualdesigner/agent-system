import logging
import time
from state import AgentState
from backend_client import call_plan as backend_call_plan
from sse import emitter
from models import SSEEvent

logger = logging.getLogger("orchestrator.nodes.call_plan")


async def call_plan_node(state: AgentState) -> dict:
    if state.get("cancelled"):
        logger.warning("[run_id=%s] call_plan cancelled", state.get("run_id"))
        return {"phase": "cancelled", "_next_node": "return_result"}

    run_id = state["run_id"] or ""
    phase = "planning"

    await emitter.emit(
        run_id,
        SSEEvent(type="node_start", node="call_plan", phase=phase, run_id=run_id),
    )

    input_data = {"task": state["task"]}

    await emitter.emit(
        run_id,
        SSEEvent(
            type="tool_call", node="call_plan", phase=phase,
            run_id=run_id,
            data={"tool": "/agent/plan", "input": input_data},
        ),
    )

    start = time.time()
    try:
        result = await backend_call_plan(state["task"], run_id=run_id)
        latency = int((time.time() - start) * 1000)
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        logger.error("[run_id=%s] call_plan failed: %s", run_id, str(e))
        await emitter.emit(
            run_id,
            SSEEvent(type="node_end", node="call_plan", phase="error", run_id=run_id, data={"error": str(e)}),
        )
        return _error_state(state, run_id, "call_plan", str(e), input_data, latency)

    logger.info("[run_id=%s] call_plan ok latency=%dms", run_id, latency)

    output = {"backend_run_id": result.get("run_id"), "plan": result.get("plan")}
    trace_entry = {"node": "call_plan", "input": input_data, "output": output, "latency_ms": latency}
    trace = (state.get("trace") or []) + [trace_entry]

    await emitter.emit(
        run_id,
        SSEEvent(
            type="node_end", node="call_plan", phase=phase,
            run_id=run_id,
            data=output,
        ),
    )

    if result.get("status") == "rejected":
        reason = result.get("reason", "plan_rejected")
        logger.warning("[run_id=%s] plan rejected: %s", run_id, reason)
        return {
            "backend_run_id": result.get("run_id"),
            "plan": result.get("plan"),
            "error": reason,
            "trace": trace[-50:],
            "phase": "error",
            "_next_node": "return_result",
        }

    return {
        "backend_run_id": result.get("run_id"),
        "plan": result.get("plan"),
        "trace": trace[-50:],
        "phase": phase,
    }


def _error_state(state, run_id, node, error, input_data, latency):
    logger.error("[run_id=%s] %s error: %s", run_id, node, error)
    trace_entry = {"node": node, "input": input_data, "output": {"error": error}, "latency_ms": latency}
    trace = (state.get("trace") or []) + [trace_entry]
    return {
        "error": error,
        "trace": trace[-50:],
        "phase": "error",
        "_next_node": "return_result",
    }
