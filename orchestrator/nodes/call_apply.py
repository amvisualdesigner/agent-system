import asyncio
import logging
import time
from state import AgentState
from backend_client import call_apply as backend_call_apply, get_run as backend_get_run
from sse import emitter
from models import SSEEvent

logger = logging.getLogger("orchestrator.nodes.call_apply")

RETRY_GETRUN_ATTEMPTS = 3
RETRY_GETRUN_DELAY_MS = 500


async def call_apply_node(state: AgentState) -> dict:
    assert state.get("run_id") is not None, "run_id must not be None"
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

    logger.info("[run_id=%s] call_apply ok latency=%dms status=%s", run_id, latency, apply_result.get("status"))

    output = {
        "execution_status": apply_result.get("status"),
        "operations": apply_result.get("operations") or apply_result.get("execution"),
    }

    run_details = None
    try:
        run_details = await _get_run_with_retry(run_id, run_id)
        if run_details:
            output["diff"] = run_details.get("diff")
            output["files"] = run_details.get("files")
            output["has_commit"] = run_details.get("has_commit")
    except Exception as e:
        logger.warning("[run_id=%s] get_run failed after retry, degrading gracefully: %s", run_id, str(e))

    trace_entry = {"node": "call_apply", "input": input_data, "output": output, "latency_ms": latency}
    trace = (state.get("trace") or []) + [trace_entry]

    await emitter.emit(
        run_id,
        SSEEvent(type="node_end", node="call_apply", phase=phase, run_id=run_id, data=output),
    )

    return {
        **state,
        "execution": apply_result,
        "run_details": run_details,
        "trace": trace[-50:],
        "phase": phase,
        "_next_node": "return_result",
    }


async def _get_run_with_retry(run_id: str, target_run_id: str, attempts: int = RETRY_GETRUN_ATTEMPTS):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            data = await backend_get_run(target_run_id)
            if data.get("exists"):
                return data
            logger.info(
                "[run_id=%s] get_run attempt=%d/%d run not yet available, retrying",
                run_id, attempt, attempts,
            )
        except Exception as e:
            last_error = e
            logger.warning(
                "[run_id=%s] get_run attempt=%d/%d failed: %s",
                run_id, attempt, attempts, str(e),
            )
        if attempt < attempts:
            await asyncio.sleep(RETRY_GETRUN_DELAY_MS / 1000)

    if last_error:
        raise last_error
    raise RuntimeError(f"get_run not available after {attempts} attempts")


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
