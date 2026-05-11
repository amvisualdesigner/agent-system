from fastapi import APIRouter

from app.engine.apply_engine import apply_engine
from app.contracts.apply_request import ApplyRequest
from app.planner.context_builder import build_context

router = APIRouter()

@router.post("/agent/apply")
def agent_apply(req: ApplyRequest):

    base_dir = f"/tmp/agent-runs/{req.run_id}"

    context = build_context(base_dir, req.run_id)

    return apply_engine(req.plan, context)