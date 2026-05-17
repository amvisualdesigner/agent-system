import uuid
import os

from fastapi import APIRouter
from app.planner.plan_generator import generate_plan
from app.planner.plan_validator import validate_plan, prune_scaffold
from app.planner.task_classifier import classify_task
from app.policy.policy import MAX_SCAFFOLD_OPS_PER_RUN
from app.contracts.plan_request import PlanRequest
from app.utils.workspace import list_workspace_files
from app.runtime.context import build_context
from app.utils.state import write_state

router = APIRouter()

@router.post("/agent/plan")
def agent_plan(req: PlanRequest):

    run_id = req.run_id or str(uuid.uuid4())

    context = build_context(run_id)

    workspace_files = list_workspace_files(context.workspace)

    classification = classify_task(req.task)

    plan = generate_plan(req, workspace_files, task_mode=classification.mode)

    write_state(run_id, "plan")

    # Hybrid soft-hard constraint: prune scaffold budget instead of rejecting
    llm_feedback = ""
    if "actions" in plan:
        prune_scaffold(plan)

    ok, reason = validate_plan(plan)

    if not ok:
        return {
            "run_id": run_id,
            "status": "rejected",
            "reason": reason,
            "plan": plan,
            "planner_meta": {
                "task_mode": classification.mode,
                "semantic_score": classification.semantic_score,
                "composition_score": classification.composition_score,
                "matched_patterns": classification.matched_patterns,
            },
        }

    if plan.get("pruned"):
        scaffold_count = len([a for a in plan["actions"] if a.get("intent") == "scaffold"])
        llm_feedback = (
            f"Your previous plan exceeded scaffold budget "
            f"({MAX_SCAFFOLD_OPS_PER_RUN} max). "
            f"Pruned {plan['pruned_count']} scaffold operations. "
            f"Final scaffold count: {scaffold_count}. "
            f"You MUST reduce scaffold operations in your next plan."
        )

    return {
        "run_id": run_id,
        "status": "ok",
        "plan": plan,
        "llm_feedback": llm_feedback,
        "planner_meta": {
            "task_mode": classification.mode,
            "semantic_score": classification.semantic_score,
            "composition_score": classification.composition_score,
            "matched_patterns": classification.matched_patterns,
        },
    }