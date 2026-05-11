import uuid
import os

from fastapi import APIRouter
from app.planner.plan_generator import generate_plan
from app.planner.plan_validator import validate_plan
from app.contracts.plan_request import PlanRequest
from app.utils.workspace import list_workspace_files
from app.runtime.context import build_context
from app.utils.state import write_state

router = APIRouter()

@router.post("/agent/plan")
def agent_plan(req: PlanRequest):

    run_id = str(uuid.uuid4())
    base_dir = f"/tmp/agent-runs/{run_id}"

    context = build_context(run_id)

    workspace_files = list_workspace_files(context.workspace)

    plan = generate_plan(req, workspace_files)

    write_state(run_id, "plan")

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