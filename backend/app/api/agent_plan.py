import uuid
import os

from fastapi import APIRouter
from app.planner.plan_generator import generate_plan
from app.planner.plan_validator import validate_plan
from app.planner.context_builder import build_context
from app.contracts.plan_request import PlanRequest

router = APIRouter()

@router.post("/agent/plan")
def agent_plan(req: PlanRequest):

    run_id = str(uuid.uuid4())
    base_dir = f"/tmp/agent-runs/{run_id}"

    context = build_context(base_dir, run_id)

    plan = generate_plan(req, context)

    ok, reason = validate_plan(plan)

    if not ok:
        return {
            "run_id": run_id,
            "status": "rejected",
            "reason": reason,
            "plan": plan
        }

    return {
        "run_id": run_id,
        "status": "ok",
        "plan": plan
    }