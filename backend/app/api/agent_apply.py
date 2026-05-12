from fastapi import APIRouter

from app.engine.apply_engine import apply_engine
from app.contracts.apply_request import ApplyRequest
from app.runtime.context import build_context
from app.executor.worktree_manager import create_worktree

router = APIRouter()

@router.post("/agent/apply")
def agent_apply(req: ApplyRequest):

    context = build_context(req.run_id)
    context.workspace = create_worktree(req.run_id)

    return apply_engine(req.run_id, req.plan, context)