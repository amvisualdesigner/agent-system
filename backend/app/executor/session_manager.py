"""Physical Session operations: create worktree/branch, resolve, ensure.

Session = physical continuity + lifecycle. This module owns the ONLY creation
of a Session workspace/branch and NEVER destroys or silently resyncs an
existing workspace (no checkout master, no reset --hard, no rm -rf).
"""

from __future__ import annotations

import os
import subprocess

from app.config.settings import settings
from app.executor.worktree_manager import ensure_worktree, create_worktree  # noqa: F401 (legacy run-only path)
from app.session.models import SessionRecord, SessionStatus
from app.state.session_state import (
    create_session_record,
    load_session,
    transition_session,
)
from app.utils.path_guard import guard_within
from app.utils.session_id import validate_session_id


class SessionPhysicalError(RuntimeError):
    """A Session's physical continuity is inconsistent or missing."""


def session_workspace(session_id: str) -> str:
    validate_session_id(session_id)
    workspace = f"{settings.RUNS_DIR}/{session_id}"
    guard_within(workspace, settings.RUNS_DIR)
    return workspace


def session_branch_for(session_id: str) -> str:
    validate_session_id(session_id)
    return f"agent/session-{session_id[:8]}"


def _branch_exists(repo_root: str, branch: str) -> bool:
    r = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_root, capture_output=True, text=True,
    )
    return r.returncode == 0


def _is_worktree_dir(workspace: str) -> bool:
    return os.path.isdir(workspace) and os.path.isfile(os.path.join(workspace, ".git"))


def _create_session_worktree(repo_root: str, workspace: str, branch: str, base_branch: str) -> None:
    subprocess.run(["git", "worktree", "prune"], cwd=repo_root, check=False)
    subprocess.run(
        ["git", "worktree", "add", workspace, "-b", branch, base_branch],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "config", "user.email", "agent@local"], cwd=workspace, check=False)
    subprocess.run(["git", "config", "user.name", "agent"], cwd=workspace, check=False)


def _mark_failed_if_record(session_id: str, workspace: str, branch: str) -> None:
    record = load_session(session_id)
    if record is None or record.status == SessionStatus.FAILED:
        return
    transition_session(session_id, SessionStatus.FAILED)


def create_session(session_id: str, base_branch: str = "master") -> SessionRecord:
    """Create a Session: ACTIVE record + one worktree/branch from base.

    Idempotent reuse ONLY when the physical relationship matches exactly.
    Never destroys or repairs an existing workspace; on mismatch the Session
    is marked FAILED (never auto-deleted).
    """
    session_id = validate_session_id(session_id)
    workspace = session_workspace(session_id)
    branch = session_branch_for(session_id)
    repo_root = settings.REPO_ROOT

    existing = load_session(session_id)
    workspace_exists = _is_worktree_dir(workspace)

    if existing is None and not workspace_exists:
        _create_session_worktree(repo_root, workspace, branch, base_branch)
        record = SessionRecord(
            session_id=session_id,
            base_branch=base_branch,
            branch=branch,
            workspace=workspace,
            status=SessionStatus.ACTIVE,
        )
        create_session_record(record)
        loaded = load_session(session_id)
        if loaded is None:
            raise SessionPhysicalError(f"Session record failed to persist: {session_id}")
        return loaded

    if existing is not None and workspace_exists:
        if existing.branch == branch and existing.workspace == workspace and _branch_exists(repo_root, branch):
            return existing
        _mark_failed_if_record(session_id, workspace, branch)
        raise SessionPhysicalError(
            f"Session {session_id}: physical relationship mismatch (workspace={workspace} exists, "
            f"branch={branch}). Refusing to reuse or repair silently."
        )

    reminder = (
        "Refusing to auto-create/delete. Recovery of a Session is an explicit operation."
    )
    if existing is not None and not workspace_exists:
        _mark_failed_if_record(session_id, workspace, branch)
        raise SessionPhysicalError(f"Session {session_id}: workspace {workspace} is missing. {reminder}")
    if existing is None and workspace_exists:
        raise SessionPhysicalError(
            f"Session {session_id}: physical orphan — workspace {workspace} exists but no Session record. "
            f"{reminder}"
        )
    raise SessionPhysicalError(f"Session {session_id}: unreachable physical state. {reminder}")


def resolve_session(session_id: str) -> SessionRecord:
    """Load a Session record by id. Raises if unknown."""
    session_id = validate_session_id(session_id)
    record = load_session(session_id)
    if record is None:
        raise SessionPhysicalError(f"Session not found: {session_id}")
    return record


def ensure_session_worktree(record: SessionRecord) -> str:
    """Ensure the Session workspace exists and matches the record.

    For Session: if it exists → use it; if it is missing → explicit error.
    Never recreates silently (recovery is an explicit Session operation).
    """
    if record.workspace and _is_worktree_dir(record.workspace):
        return record.workspace
    raise SessionPhysicalError(
        f"Session {record.session_id}: session worktree missing at {record.workspace!r}. "
        "Session worktrees are never recreated silently."
    )


__all__ = [
    "SessionPhysicalError",
    "session_workspace",
    "session_branch_for",
    "create_session",
    "resolve_session",
    "ensure_session_worktree",
]