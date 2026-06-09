import logging
import time
from state import AgentState
from backend_client import call_confirm
from sse import emitter
from models import SSEEvent

logger = logging.getLogger("orchestrator.nodes.confirm")


async def confirm_node(state: AgentState) -> dict:
    assert state.get("run_id"), "run_id must be set and non-empty"
    run_id = state["run_id"]
    logger.info("node=confirm run_id=%s", run_id)

    if state.get("cancelled"):
        logger.warning("[run_id=%s] confirm cancelled", run_id)
        return {**state, "phase": "cancelled", "_next_node": "return_result"}

    if state.get("error"):
        logger.warning("[run_id=%s] confirm skipped (prior error)", run_id)
        return {**state, "phase": "error", "_next_node": "return_result"}

    interpretation = state.get("interpretation")
    if not interpretation:
        logger.warning("[run_id=%s] confirm: no interpretation", run_id)
        return {**state, "error": "no_interpretation", "phase": "error", "_next_node": "return_result"}

    phase = "confirming"
    await emitter.emit(
        run_id,
        SSEEvent(type="node_start", node="confirm", phase=phase, run_id=run_id),
    )

    contract_id = state.get("confirmed_intent", {}).get("contract_id") or interpretation.get("contract_id", "")
    if not contract_id:
        logger.warning("[run_id=%s] confirm: no contract_id (interpretation incomplete)", run_id)
        await emitter.emit(
            run_id,
            SSEEvent(
                type="interpretation_ready", node="confirm", phase="awaiting_confirmation",
                run_id=run_id,
                data=interpretation,
            ),
        )
        return {
            **state,
            "interpretation": interpretation,
            "plan": None,
            "trace": (state.get("trace") or []) + [{
                "node": "confirm", "input": {"action": "skip_no_contract"},
                "output": {"error": "contract_id is required — please refine your request"},
                "latency_ms": 0,
            }],
            "phase": "awaiting_confirmation",
            "_next_node": "return_result",
        }

    confirmed_intent = state.get("confirmed_intent", {})
    proposed_actions = interpretation.get("proposed_actions", [])

    # Merge instance_hint from interpretation into confirmed_intent actions when missing
    ci_actions = confirmed_intent.get("actions", proposed_actions)
    if ci_actions and ci_actions is not proposed_actions:
        proposed_map = {a.get("target_capability"): a for a in proposed_actions if a.get("instance_hint")}
        for action in ci_actions:
            cap = action.get("target_capability")
            if cap in proposed_map and "instance_hint" not in action:
                action["instance_hint"] = proposed_map[cap]["instance_hint"]

    confirm_payload = {
        "run_id": run_id,
        "interpretation_id": run_id,
        "contract_id": contract_id,
        "contract_version": confirmed_intent.get("contract_version", interpretation.get("contract_version", 1)),
        "actions": ci_actions,
        "params": confirmed_intent.get("params", interpretation.get("params_proposed", {})),
        "user_message": confirmed_intent.get("user_message", ""),
    }

    input_data = {"confirm_payload": confirm_payload}
    await emitter.emit(
        run_id,
        SSEEvent(
            type="tool_call", node="confirm", phase=phase,
            run_id=run_id,
            data={"tool": "/agent/confirm", "input": input_data},
        ),
    )

    start = time.time()
    try:
        confirm_result = await call_confirm(confirm_payload)
        latency = int((time.time() - start) * 1000)
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        logger.error("[run_id=%s] confirm failed: %s", run_id, str(e))
        await emitter.emit(
            run_id,
            SSEEvent(type="node_end", node="confirm", phase="error", run_id=run_id, data={"error": str(e)}),
        )
        return _error_state(state, run_id, "confirm", str(e), input_data, latency)

    logger.info("[run_id=%s] confirm ok latency=%dms", run_id, latency)

    output = {"confirm_result": confirm_result}
    trace_entry = {"node": "confirm", "input": input_data, "output": output, "latency_ms": latency}
    trace = (state.get("trace") or []) + [trace_entry]

    await emitter.emit(
        run_id,
        SSEEvent(type="node_end", node="confirm", phase=phase, run_id=run_id, data=output),
    )

    status = confirm_result.get("status", "ok")
    if status == "rejected":
        reason = confirm_result.get("reason", "confirm_rejected")
        logger.warning("[run_id=%s] confirm rejected: %s", run_id, reason)
        return {
            **state,
            "error": reason,
            "trace": trace[-50:],
            "phase": "error",
            "_next_node": "return_result",
        }

    plan = confirm_result.get("plan", {})
    plan_preview = confirm_result.get("plan_preview", {})
    gate = confirm_result.get("gate", {})

    await emitter.emit(
        run_id,
        SSEEvent(
            type="plan_preview_ready", node="confirm", phase="awaiting_apply",
            run_id=run_id, data={"plan": plan, "plan_preview": plan_preview, "gate": gate},
        ),
    )

    return {
        **state,
        "plan": plan,
        "confirmed_intent": confirmed_intent,
        "plan_preview": plan_preview,
        "trace": trace[-50:],
        "phase": "awaiting_apply",
        "_next_node": "validate_plan",
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
