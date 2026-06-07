import os
from dataclasses import dataclass

from app.config.settings import settings
from app.utils.path_guard import guard_within

@dataclass
class RunContext:
    run_id: str
    base_dir: str
    workspace: str
    artifacts: str
    worktree_created: bool = False


def build_context(run_id: str, workspace_root: str | None = None) -> RunContext:
    workspace = f"{settings.RUNS_DIR}/{run_id}"
    artifacts = f"{settings.ARTIFACTS_DIR}/{run_id}"

    guard_within(workspace, settings.RUNS_DIR)
    guard_within(artifacts, settings.ARTIFACTS_DIR)

    os.makedirs(artifacts, exist_ok=True)

    return RunContext(
        run_id=run_id,
        base_dir=workspace,
        workspace=workspace,
        artifacts=artifacts
    )