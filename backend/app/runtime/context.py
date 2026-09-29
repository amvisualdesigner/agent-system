import os
from dataclasses import dataclass

from app.config.settings import settings
from app.session.models import SessionStatus
from app.utils.path_guard import guard_within
from app.utils.session_id import validate_session_id


@dataclass
class RunContext:
    run_id: str
    base_dir: str
    workspace: str
    artifacts: str
    worktree_created: bool = False
    session_id: str | None = None
    session_branch: str | None = None


def build_context(
    run_id: str,
    workspace_root: str | None = None,
    session_id: str | None = None,
) -> RunContext:
    """Resolve the physical workspace for a Run.

    Run WITH Session → workspace = session.workspace (record must exist and be
    ACTIVE; recovery is explicit, never silent). Run WITHOUT Session → legacy
    per-run workspace {RUNS_DIR}/{run_id}.
    """
    session_branch = None
    if session_id is not None:
        validate_session_id(session_id)
        from app.state.session_state import load_session

        record = load_session(session_id)
        if record is None:
            raise ValueError(f"Session not found: {session_id}")
        if record.status != SessionStatus.ACTIVE:
            raise ValueError(
                f"Session {session_id} is '{record.status.value}'. Active Sessions only."
            )
        workspace = record.workspace
        session_branch = record.branch
    elif workspace_root:
        workspace = workspace_root
    else:
        workspace = f"{settings.RUNS_DIR}/{run_id}"

    artifacts = f"{settings.ARTIFACTS_DIR}/{run_id}"

    guard_within(workspace, settings.RUNS_DIR)
    guard_within(artifacts, settings.ARTIFACTS_DIR)

    os.makedirs(artifacts, exist_ok=True)

    return RunContext(
        run_id=run_id,
        base_dir=workspace,
        workspace=workspace,
        artifacts=artifacts,
        session_id=session_id,
        session_branch=session_branch,
    )
