import logging
from state import AgentState
from sse import emitter
from models import SSEEvent

logger = logging.getLogger("orchestrator.nodes.validate_plan")


async def validate_plan_node(state: AgentState) -> dict:
    assert state.get("run_id"), "run_id must be set and non-empty"
    run_id = state["run_id"]
    logger.info("node=validate_plan run_id=%s", run_id)

    phase = state.get("phase", "confirming")

    if state.get("cancelled"):
        logger.warning("[run_id=%s] validate_plan cancelled", run_id)
        return {**state, "phase": "cancelled", "_next_node": "return_result"}

    if state.get("error"):
        logger.warning("[run_id=%s] validate_plan skipped (prior error)", run_id)
        return {**state, "phase": "error", "_next_node": "return_result"}

    await emitter.emit(
        run_id,
        SSEEvent(type="node_start", node="validate_plan", phase=phase, run_id=run_id),
    )

    plan = state.get("plan")
    input_data = {"plan_exists": plan is not None}

    if plan is None:
        logger.warning("[run_id=%s] validate_plan: no plan", run_id)
        output = {"valid": False, "reason": "no_plan"}
        trace = _add_trace(state, "validate_plan", input_data, output, 0)
        await emitter.emit(run_id, SSEEvent(type="node_end", node="validate_plan", phase=phase, run_id=run_id, data=output))
        return {**state, "trace": trace[-50:], "phase": "error", "error": "no_plan", "_next_node": "return_result"}

    # Compiled plan from /agent/confirm
    actions = plan.get("actions", [])
    skill_ir = plan.get("skill_ir")
    has_valid_skill_ir = (
        skill_ir
        and isinstance(skill_ir, dict)
        and skill_ir.get("contract_id")
        and skill_ir.get("confidence", 0) >= 0.5
    )
    valid = has_valid_skill_ir or len(actions) > 0

    if valid:
        logger.info("[run_id=%s] validate_plan: valid (%d actions)", run_id, len(actions))
        output = {"valid": True}
        trace = _add_trace(state, "validate_plan", input_data, output, 0)
        await emitter.emit(run_id, SSEEvent(type="node_end", node="validate_plan", phase=phase, run_id=run_id, data=output))
        # Go to awaiting_apply — user must trigger apply via POST /run/{id}/apply
        return {**state, "trace": trace[-50:], "phase": "awaiting_apply", "_next_node": "return_result"}

    logger.warning("[run_id=%s] validate_plan: invalid plan (no actions, no skill_ir)", run_id)
    output = {"valid": False, "reason": "no_valid_actions"}
    trace = _add_trace(state, "validate_plan", input_data, output, 0)
    await emitter.emit(run_id, SSEEvent(type="node_end", node="validate_plan", phase=phase, run_id=run_id, data=output))
    return {**state, "trace": trace[-50:], "_next_node": "return_result", "error": "no_valid_actions", "phase": "error"}


def _add_trace(state, node, input_data, output, latency):
    entry = {"node": node, "input": input_data, "output": output, "latency_ms": latency}
    return (state.get("trace") or []) + [entry]
