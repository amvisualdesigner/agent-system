import logging
import time
from state import AgentState
from backend_client import call_apply as backend_call_apply
from sse import emitter
from models import SSEEvent

logger = logging.getLogger("orchestrator.nodes.call_apply")


async def call_apply_node(state: AgentState) -> dict:
    assert state.get("run_id"), "run_id must be set and non-empty"
    run_id = state["run_id"]
    logger.info("node=call_apply run_id=%s", run_id)

    backend_run_id = state.get("backend_run_id", "")
    phase = "executing"
    input_data = {"backend_run_id": backend_run_id, "plan": state.get("plan")}

    if state.get("cancelled"):
        logger.warning("[run_id=%s] call_apply cancelled", run_id)
        return {**state, "phase": "cancelled", "_next_node": "return_result"}

    if state.get("error"):
        logger.warning("[run_id=%s] call_apply skipped (prior error)", run_id)
        return {**state, "phase": "error", "_next_node": "return_result"}

    if state.get("execution") is not None:
        logger.info("[run_id=%s] call_apply already executed, skipping", run_id)
        return {**state, "phase": state.get("phase", "completed"), "_next_node": "return_result"}

    await emitter.emit(
        run_id,
        SSEEvent(type="node_start", node="call_apply", phase=phase, run_id=run_id),
    )

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
        apply_result = await backend_call_apply(run_id, state["plan"])
        latency = int((time.time() - start) * 1000)
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        logger.error("[run_id=%s] call_apply failed: %s", run_id, str(e))
        await emitter.emit(
            run_id,
            SSEEvent(type="node_end", node="call_apply", phase="error", run_id=run_id, data={"error": str(e)}),
        )
        return _error_state(state, run_id, "call_apply", str(e), input_data, latency)

    logger.info("[run_id=%s] call_apply ok latency=%dms", run_id, latency)

    exec_block = apply_result.get("execution", {})
    context_block = apply_result.get("context", {})
    meta_block = apply_result.get("meta")

    output = {
        "execution": exec_block,
        "context": context_block,
    }
    if meta_block:
        output["meta"] = meta_block

    trace_entry = {"node": "call_apply", "input": input_data, "output": output, "latency_ms": latency}
    trace = (state.get("trace") or []) + [trace_entry]

    await emitter.emit(
        run_id,
        SSEEvent(type="node_end", node="call_apply", phase=phase, run_id=run_id, data=output),
    )

    return {
        **state,
        "execution": apply_result,
        "trace": trace[-50:],
        "phase": phase,
        "_next_node": "return_result",
    }


def _error_state(state, run_id, node, error, input_data, latency):
    logger.error("[run_id=%s] %s error: %s", run_id, node, error)
    trace_entry = {"node": node, "input": input_data, "output": {"error": error}, "latency_ms": latency}
    trace = (state.get("trace") or []) + [trace_entry]
    return {
        **state,
        "error": error,
        "trace": trace[-50:],
        "phase": "error",
        "_next_node": "return_result",
    }
