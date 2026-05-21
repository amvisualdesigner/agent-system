"""ExecutionContext — formal execution isolation context.

Every run produces exactly one ExecutionContext.
All IO operations MUST reference this context.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


def _get_settings():
    """Lazy import to avoid eager settings resolution at module load."""
    from app.config.settings import settings as _s
    return _s


@dataclass(frozen=True)
class ExecutionContext:
    """Formal execution isolation context.

    Wraps run_id, workspace_root, worktree_id into a single
    value object that flows through the entire pipeline.

    Guarantees:
    - Every file operation is scoped to workspace_root
    - Memory persistence goes to a well-known location
    - Artifacts are written to a run-specific directory
    """
    run_id: str
    workspace_root: str
    worktree_id: str = ""
    git_ref: str | None = None
    is_isolated: bool = True
    artifacts_dir: str = ""

    def __post_init__(self):
        object.__setattr__(self, "worktree_id",
                           self.worktree_id or f"agent-{self.run_id[:8]}")

    @property
    def memory_path(self) -> str:
        return os.path.join(self.workspace_root, ".opencode", "semantic_memory.json")

    @property
    def artifacts(self) -> str:
        if self.artifacts_dir:
            return self.artifacts_dir
        settings = _get_settings()
        return os.path.join(
            getattr(settings, "ARTIFACTS_DIR", "/tmp/artifacts"), self.run_id
        )

    def guard(self, path: str) -> str:
        """Ensure path is within workspace. Raises ValueError if not."""
        resolved = os.path.realpath(os.path.join(self.workspace_root, path))
        workspace_real = os.path.realpath(self.workspace_root)
        if not resolved.startswith(workspace_real + os.sep) and resolved != workspace_real:
            raise ValueError(
                f"Path escape detected: {path} resolves to {resolved}, "
                f"which is outside workspace {workspace_real}"
            )
        return path
