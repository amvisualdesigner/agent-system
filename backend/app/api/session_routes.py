"""Session API — lifecycle + physical continuity ONLY.

Endpoints (S1-B):
    POST /session                  → create (ACTIVE + worktree + branch)
    GET  /session/{session_id}     → status + derived merge_ready/reasons
    POST /session/{session_id}/merge → single integration route (D8a: --no-ff)

Session integration happens ONLY here. No second merge route exists.
"""

from __future__ import annotations

import logging
import subprocess
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.settings import settings
from app.executor.session_manager import (
    SessionPhysicalError,
    create_session,
    resolve_session,
)
from app.intent.models import RunPhase
from app.session.locks import session_apply_lock
from app.session.models import SessionRecord, SessionStatus
from app.state.session_state import load_session, save_session, transition_session
from app.state.run_state import load_run_state
from app.utils.session_id import validate_session_id

logger = logging.getLogger(__name__)

router = APIRouter()

_TERMINAL_RUN_PHASES = {
    RunPhase.COMPLETED,
    RunPhase.FAILED,
    RunPhase.CANCELLED,
}


class CreateSessionRequest(BaseModel):
    base_branch: str = "master"


class SessionRunInfo(BaseModel):
    run_id: str
    phase: str | None = None


def _branch_exists(repo_root: str, branch: str) -> bool:
    r = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_root, capture_output=True, text=True,
    )
    return r.returncode == 0


def _worktree_exists(record: SessionRecord) -> bool:
    from app.executor.session_manager import _is_worktree_dir
    return bool(record.workspace) and _is_worktree_dir(record.workspace)


def _base_on_master(repo_root: str) -> bool:
    r = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo_root, capture_output=True, text=True,
    )
    return r.returncode == 0 and r.stdout.strip() == "master"


def _worktree_clean(workspace: str) -> bool:
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=workspace, capture_output=True, text=True,
    )
    return r.returncode == 0 and not r.stdout.strip()


def _run_phase(run_id: str) -> str | None:
    state = load_run_state(run_id)
    if state is None:
        return None
    return state.get("phase")


def _non_terminal_runs(record: SessionRecord) -> list[str]:
    blockers = []
    for rid in record.run_ids:
        phase_str = _run_phase(rid)
        if phase_str is None:
            continue
        try:
            phase = RunPhase(phase_str)
        except ValueError:
            continue
        if phase not in _TERMINAL_RUN_PHASES:
            blockers.append(rid)
    return blockers


def _merge_preconditions(record: SessionRecord) -> list[str]:
    """Return list of unmet merge preconditions (empty = merge ready)."""
    reasons: list[str] = []
    if record is None:
        return ["session_missing"]
    if record.status not in (SessionStatus.ACTIVE, SessionStatus.CONFLICT):
        reasons.append(f"session_status:{record.status.value}")
    repo_root = settings.REPO_ROOT
    if record.branch and not _branch_exists(repo_root, record.branch):
        reasons.append("branch_missing")
    if not _worktree_exists(record):
        reasons.append("worktree_missing")
    if not _branch_exists(repo_root, record.base_branch):
        reasons.append("base_branch_missing")
    if not _base_on_master(repo_root):
        reasons.append("base_not_on_master")
    blockers = _non_terminal_runs(record)
    if blockers:
        reasons.append("non_terminal_runs:" + ",".join(blockers[:5]))
    if record.workspace and not _worktree_clean(record.workspace):
        reasons.append("worktree_dirty")
    if record.branch == record.base_branch:
        reasons.append("branch_equals_base")
    if record.status == SessionStatus.MERGED:
        reasons.append("already_merged")
    return reasons


@router.post("/session")
def create_session_endpoint(req: CreateSessionRequest | None = None) -> dict:
    req = req or CreateSessionRequest()
    base = req.base_branch if req.base_branch else "master"
    session_id = str(uuid.uuid4())
    try:
        record = create_session(session_id, base_branch=base)
    except (ValueError, SessionPhysicalError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    return record.to_dict()


@router.get("/session/{session_id}")
def get_session(session_id: str) -> dict:
    session_id = validate_session_id(session_id)
    record = load_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    reasons = _merge_preconditions(record)
    runs = [
        {"run_id": rid, "phase": _run_phase(rid)}
        for rid in record.run_ids
    ]
    return {
        "session_id": record.session_id,
        "base_branch": record.base_branch,
        "branch": record.branch,
        "workspace": record.workspace,
        "status": record.status.value,
        "merge_ready": record.status in (SessionStatus.ACTIVE, SessionStatus.CONFLICT) and not reasons,
        "merge_reasons": reasons,
        "runs": runs,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "merged_commit": record.merged_commit,
    }


@router.post("/session/{session_id}/merge")
def session_merge(session_id: str) -> dict:
    """Single integration route (D8a): git merge --no-ff, explicit commit.

    Preconditions are evaluated under the Session lock. Any unmet precondition
    → Session=CONFLICT (recoverable). On git conflict → git merge --abort,
    Session=CONFLICT, base restored. Never --ours/--theirs/reset --hard/
    pull --rebase/auto-resolution.
    """
    session_id = validate_session_id(session_id)
    record = resolve_session(session_id)

    if record.status in (SessionStatus.MERGED, SessionStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"Session {session_id} is '{record.status.value}' (terminal). Merge refused.",
        )

    lock = session_apply_lock(session_id)
    if not lock.acquire(blocking=False):
        transition_session(session_id, SessionStatus.CONFLICT)
        return {
            "status": "conflict",
            "reason": "session_busy",
            "detail": "Another apply/merge holds the Session lock.",
            "session_status": SessionStatus.CONFLICT.value,
        }
    try:
        reasons = _merge_preconditions(record)
        if reasons:
            transition_session(session_id, SessionStatus.CONFLICT)
            return {
                "status": "conflict",
                "reason": "preconditions",
                "detail": reasons,
                "session_status": SessionStatus.CONFLICT.value,
            }

        repo_root = settings.REPO_ROOT
        r = subprocess.run(
            ["git", "merge", "--no-ff", "-m", f"session:{session_id[:8]}", record.branch],
            cwd=repo_root, capture_output=True, text=True,
        )
        if r.returncode != 0:
            # git merge failed (typically a conflict): abort BEFORE returning.
            subprocess.run(["git", "merge", "--abort"], cwd=repo_root, check=False)
            transition_session(session_id, SessionStatus.CONFLICT)
            detail = (r.stderr or r.stdout or "git merge failed").strip()
            return {
                "status": "conflict",
                "reason": "git_merge_conflict",
                "detail": detail[:2000],
                "merged_commit": None,
                "session_status": SessionStatus.CONFLICT.value,
            }

        merged_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, check=True, capture_output=True, text=True,
        ).stdout.strip()
        transition_session(session_id, SessionStatus.MERGED)
        save_session(session_id, merged_commit=merged_commit)
        return {
            "status": "merged",
            "merged_commit": merged_commit,
            "session_status": SessionStatus.MERGED.value,
        }
    finally:
        lock.release()