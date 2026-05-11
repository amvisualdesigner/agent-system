import os
from dataclasses import dataclass

@dataclass
class RunContext:
    run_id: str
    base_dir: str
    workspace: str
    artifacts: str


def build_context(run_id: str, workspace_root: str | None = None) -> RunContext:
    base_dir = workspace_root or f"/tmp/agent-runs/{run_id}"

    workspace = os.path.join(base_dir, "workspace")
    artifacts = os.path.join(base_dir, "artifacts")

    os.makedirs(workspace, exist_ok=True)
    os.makedirs(artifacts, exist_ok=True)

    return RunContext(
        run_id=run_id,
        base_dir=base_dir,
        workspace=workspace,
        artifacts=artifacts
    )