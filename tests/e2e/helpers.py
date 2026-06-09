"""Shared helpers for E2E pipeline tests — interpret + confirm simulation.

These mirror the logic in tests/intent/test_e2e_flow.py but without
importing from a test module.
"""

from app.intent.models import ConfirmedIntent, IntentAction
from app.intent.plan_compiler import compile_plan
from app.contracts.skill_registry import get_contract
from app.catalog.loader import get_contract_catalog
from app.intent.interpreter import _select_contract, _has_action_verb


def simulate_interpret(message: str) -> dict:
    contract_id = _select_contract(message)
    if not contract_id:
        return {"status": "unsupported", "contract_id": ""}
    catalog_entry = get_contract_catalog(contract_id)
    if not catalog_entry:
        return {"status": "unsupported"}
    if not _has_action_verb(message):
        return {"status": "needs_clarification", "contract_id": contract_id}

    lower = message.lower()
    actions = []
    params = {}

    if "line chart" in lower or "linechart" in lower or "timeseries" in lower or "trend chart" in lower or "chart" in lower:
        instance_hint = None
        if "line chart" in lower or "linechart" in lower:
            instance_hint = "linechart"
        elif "timeseries" in lower:
            instance_hint = "timeseries"
        elif "trend" in lower:
            instance_hint = "timeseries"
        action = {"verb": "", "target_capability": "presentation.timeseries", "label": "Trend chart", "confidence": 0.9}
        if instance_hint:
            action["instance_hint"] = instance_hint
        if any(v in lower for v in ["remove", "delete", "hide"]):
            action["verb"] = "remove"
            actions.append(action)
        elif any(v in lower for v in ["add", "create", "include", "insert"]):
            action["verb"] = "create"
            actions.append(action)
    if "kpi" in lower or "metric" in lower:
        if any(v in lower for v in ["remove", "delete", "hide"]):
            actions.append({"verb": "remove", "target_capability": "presentation.kpi_row", "label": "KPI row", "confidence": 0.9})
        elif any(v in lower for v in ["update", "modify", "change", "set"]):
            actions.append({"verb": "modify", "target_capability": "presentation.kpi_row", "label": "KPI row", "confidence": 0.9})
            if "revenue" in lower:
                params["metrics"] = ["revenue"]
            if "growth" in lower:
                params.setdefault("metrics", []).append("growth")
    if "dashboard" in lower or "page" in lower:
        if any(v in lower for v in ["update", "modify", "change", "edit"]):
            actions.append({"verb": "modify", "target_capability": "layout.page", "label": "Dashboard page", "confidence": 0.9})

    return {
        "status": "ok",
        "contract_id": contract_id,
        "contract_version": 1,
        "proposed_actions": actions,
        "params_proposed": params,
    }


def simulate_confirm(interpret_result: dict) -> dict:
    contract_id = interpret_result.get("contract_id", "")
    actions_data = interpret_result.get("proposed_actions", [])
    params = interpret_result.get("params_proposed", {})

    if not actions_data:
        return {"status": "rejected", "reason": "no_actions"}

    contract = get_contract(contract_id, 1)
    if not contract:
        return {"status": "rejected", "reason": "contract_not_found"}

    clean_actions = []
    for a in actions_data:
        entry = {
            "verb": a.get("verb", ""),
            "target_capability": a.get("target_capability", ""),
            "params": a.get("params", {}),
            "confidence": a.get("confidence", 1.0),
        }
        if a.get("instance_hint"):
            entry["instance_hint"] = a["instance_hint"]
        clean_actions.append(entry)
    confirmed = ConfirmedIntent(
        contract_id=contract_id,
        contract_version=1,
        actions=[IntentAction(**a) for a in clean_actions],
        params=params,
        user_message="test",
        interpretation_id="e2e-test",
    )

    try:
        plan = compile_plan(confirmed)
    except ValueError as e:
        return {"status": "rejected", "reason": str(e)}

    summary = "; ".join(f"{a.verb.capitalize()} {a.target_capability}" for a in confirmed.actions)

    structural_ops = []
    for a in confirmed.actions:
        op_verb = "DELETE" if a.verb == "remove" else a.verb.upper()
        structural_ops.append({"action": op_verb, "target": a.target_capability})

    return {
        "status": "ok",
        "plan": plan.to_dict(),
        "plan_preview": {
            "summary_human": summary,
            "structural_operations": structural_ops,
            "estimated_files": [],
        },
        "gate": {"blocked": False},
    }
