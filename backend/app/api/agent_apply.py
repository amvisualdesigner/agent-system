from fastapi import APIRouter, HTTPException

from app.engine.apply_engine import apply_engine
from app.contracts.apply_request import ApplyRequest
from app.runtime.context import build_context
from app.executor.worktree_manager import create_worktree
from app.utils.run_id import validate_run_id

router = APIRouter()

@router.post("/agent/apply")
def agent_apply(req: ApplyRequest):

    if not req.run_id:
        raise HTTPException(status_code=400, detail="run_id is required")
    run_id = validate_run_id(req.run_id)

    context = build_context(run_id)
    context.workspace = create_worktree(run_id)

    return apply_engine(run_id, req.plan, context, dry_run=req.dry_run)