import logging
from state import AgentState
from sse import emitter
from models import SSEEvent

logger = logging.getLogger("orchestrator.nodes.validate_plan")


async def validate_plan_node(state: AgentState) -> dict:
    run_id = state.get("run_id", "")
    phase = state.get("phase", "planning")

    if state.get("cancelled"):
        logger.warning("[run_id=%s] validate_plan cancelled", run_id)
        return {"phase": "cancelled", "_next_node": "return_result"}

    if state.get("error"):
        logger.warning("[run_id=%s] validate_plan skipped (prior error)", run_id)
        return {"phase": "error", "_next_node": "return_result"}

    await emitter.emit(
        run_id,
        SSEEvent(type="node_start", node="validate_plan", phase=phase, run_id=run_id),
    )

    plan = state.get("plan")
    retry_count = state.get("retry_count", 0)
    input_data = {"plan_exists": plan is not None, "retry_count": retry_count}

    if plan is None:
        logger.warning("[run_id=%s] validate_plan: no plan", run_id)
        output = {"valid": False, "reason": "no_plan"}
        trace = _add_trace(state, "validate_plan", input_data, output, 0)
        await emitter.emit(run_id, SSEEvent(type="node_end", node="validate_plan", phase=phase, run_id=run_id, data=output))
        return {"trace": trace[-50:], "phase": "error", "error": "no_plan", "_next_node": "return_result"}

    actions = plan.get("actions", [])
    valid = len(actions) > 0 and all(a.get("file_path", "") for a in actions)

    if valid:
        logger.info("[run_id=%s] validate_plan: valid (%d actions)", run_id, len(actions))
        output = {"valid": True}
        trace = _add_trace(state, "validate_plan", input_data, output, 0)
        await emitter.emit(run_id, SSEEvent(type="node_end", node="validate_plan", phase=phase, run_id=run_id, data=output))
        return {"trace": trace[-50:], "_next_node": "call_apply"}

    if retry_count < 1:
        logger.info("[run_id=%s] validate_plan: retrying (retry_count=%d)", run_id, retry_count)
        output = {"valid": False, "decision": "retry"}
        trace = _add_trace(state, "validate_plan", input_data, output, 0)
        await emitter.emit(run_id, SSEEvent(type="node_end", node="validate_plan", phase=phase, run_id=run_id, data=output))
        return {"trace": trace[-50:], "_next_node": "call_plan", "retry_count": retry_count + 1}

    logger.warning("[run_id=%s] validate_plan: max retries exceeded", run_id)
    output = {"valid": False, "decision": "abort", "reason": "max_retries_exceeded"}
    trace = _add_trace(state, "validate_plan", input_data, output, 0)
    await emitter.emit(run_id, SSEEvent(type="node_end", node="validate_plan", phase=phase, run_id=run_id, data=output))
    return {"trace": trace[-50:], "_next_node": "return_result", "error": "max_retries_exceeded", "phase": "error"}


def _add_trace(state, node, input_data, output, latency):
    entry = {"node": node, "input": input_data, "output": output, "latency_ms": latency}
    return (state.get("trace") or []) + [entry]
