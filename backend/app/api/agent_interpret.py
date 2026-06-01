from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.intent.interpreter import interpret
from app.intent.models import RunPhase
from app.utils.run_id import validate_run_id

logger = logging.getLogger(__name__)

router = APIRouter()


class InterpretRequest(BaseModel):
    run_id: str
    message: str
    conversation: list[dict] = []


class InterpretResponse(BaseModel):
    class Config:
        arbitrary_types_allowed = True


@router.post("/agent/interpret")
def agent_interpret(req: InterpretRequest):
    if not req.run_id:
        raise HTTPException(status_code=400, detail="run_id is required")
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    run_id = validate_run_id(req.run_id)

    # Optional: load StructuralIndex snapshot for worktree_capabilities
    index_snapshot = None
    try:
        from app.engine.structural_index import StructuralIndex
        from app.runtime.context import build_context
        context = build_context(run_id)
        if context and context.workspace:
            idx = StructuralIndex.from_worktree(context.workspace)
            index_snapshot = {cap: idx.resolve_all_paths(cap) for cap in idx}
            logger.info("worktree_capabilities index: %d caps", len(index_snapshot))
    except Exception as e:
        logger.warning("Could not load StructuralIndex snapshot: %s", e)

    draft = interpret(
        message=req.message,
        conversation=req.conversation,
        index_snapshot=index_snapshot,
    )

    draft_dict = draft.to_dict()

    # Persist state: store interpretation draft, transition to awaiting_confirmation
    from app.state.run_state import save_run_state, transition_phase
    save_run_state(run_id, {
        "interpretation_draft": draft_dict,
        "phase": RunPhase.AWAITING_CONFIRMATION.value,
    })
    transition_phase(run_id, RunPhase.AWAITING_CONFIRMATION)

    return draft_dict
