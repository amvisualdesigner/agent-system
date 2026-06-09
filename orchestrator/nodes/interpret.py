import logging
import time
from state import AgentState
from backend_client import call_interpret
from sse import emitter
from models import SSEEvent

logger = logging.getLogger("orchestrator.nodes.interpret")


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
        # No retry — emit interpretation_ready with clarification question so user can refine
        logger.info("[run_id=%s] interpret: needs_clarification: %s", run_id,
                     draft.get("clarification_question", "unspecified"))
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

    if status in ("unsupported", "error"):
        logger.warning("[run_id=%s] interpret: %s — %s", run_id, status,
                        draft.get("clarification_question", ""))
        # Treat unsupported as clarification: ask user to rephrase
        clarification = draft.get("clarification_question",
                                   "I didn't understand that. Please provide more detail "
                                   "(e.g., 'remove the trend chart', 'update KPI metrics').")
        await emitter.emit(
            run_id,
            SSEEvent(
                type="interpretation_ready", node="interpret", phase="awaiting_confirmation",
                run_id=run_id, data={**draft, "clarification_question": clarification},
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
