import logging
import time
from state import AgentState
from backend_client import call_interpret
from sse import emitter
from models import SSEEvent

logger = logging.getLogger("orchestrator.nodes.interpret")

MAX_CLARIFICATION_RETRIES = 2


async def interpret_node(state: AgentState) -> dict:
    assert state.get("run_id"), "run_id must be set and non-empty"
    run_id = state["run_id"]
    logger.info("node=interpret run_id=%s", run_id)

    if state.get("cancelled"):
        logger.warning("[run_id=%s] interpret cancelled", run_id)
        return {**state, "phase": "cancelled", "_next_node": "return_result"}

    phase = "planning"
    await emitter.emit(
        run_id,
        SSEEvent(type="node_start", node="interpret", phase=phase, run_id=run_id),
    )

    input_data = {"task": state["task"], "conversation": []}
    await emitter.emit(
        run_id,
        SSEEvent(
            type="tool_call", node="interpret", phase=phase,
            run_id=run_id,
            data={"tool": "/agent/interpret", "input": input_data},
        ),
    )

    start = time.time()
    try:
        draft = await call_interpret(state["task"], run_id=run_id)
        latency = int((time.time() - start) * 1000)
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        logger.error("[run_id=%s] interpret failed: %s", run_id, str(e))
        await emitter.emit(
            run_id,
            SSEEvent(type="node_end", node="interpret", phase="error", run_id=run_id, data={"error": str(e)}),
        )
        return _error_state(state, run_id, "interpret", str(e), input_data, latency)

    logger.info("[run_id=%s] interpret ok latency=%dms", run_id, latency)

    output = {"backend_run_id": run_id, "interpretation": draft}
    trace_entry = {"node": "interpret", "input": input_data, "output": output, "latency_ms": latency}
    trace = (state.get("trace") or []) + [trace_entry]

    await emitter.emit(
        run_id,
        SSEEvent(
            type="node_end", node="interpret", phase=phase,
            run_id=run_id, data=output,
        ),
    )

    status = draft.get("status", "ok")
    if status == "needs_clarification":
        retry_count = state.get("retry_count", 0)
        if retry_count < MAX_CLARIFICATION_RETRIES:
            logger.info("[run_id=%s] interpret: needs_clarification, retry %d", run_id, retry_count)
            return {
                **state,
                "interpretation": draft,
                "trace": trace[-50:],
                "retry_count": retry_count + 1,
                "_next_node": "interpret",
            }
        logger.warning("[run_id=%s] interpret: max clarification retries", run_id)
        return {
            **state,
            "interpretation": draft,
            "error": "max_clarification_retries",
            "trace": trace[-50:],
            "phase": "error",
            "_next_node": "return_result",
        }

    if status in ("unsupported", "error"):
        logger.warning("[run_id=%s] interpret: %s", run_id, status)
        return {
            **state,
            "interpretation": draft,
            "error": draft.get("clarification_question", "unsupported"),
            "trace": trace[-50:],
            "phase": "error",
            "_next_node": "return_result",
        }

    # Emit interpretation_ready event before awaiting confirmation
    await emitter.emit(
        run_id,
        SSEEvent(
            type="interpretation_ready", node="interpret", phase="awaiting_confirmation",
            run_id=run_id, data=draft,
        ),
    )

    return {
        **state,
        "interpretation": draft,
        "plan": None,
        "trace": trace[-50:],
        "phase": "awaiting_confirmation",
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
