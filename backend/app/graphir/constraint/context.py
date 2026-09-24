"""ExecutionContext + PipelineState + RenderContext.

Three-layer context hierarchy:

  ExecutionContext (workspace-level)
    ↓
  PipelineState (pipeline-level state: file_nodes, decisions, ...)
    ↓
  RenderContext (renderer-level projection: PipelineState + feature_flags)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from app.graphir.models import PipelineRoute


def _get_settings():
    """Lazy import to avoid eager settings resolution at module load."""
    from app.config.settings import settings as _s
    return _s


# ── Layer 1: Execution context (workspace isolation) ──────────────


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
    active_route: PipelineRoute = "unknown"

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
        s = _get_settings()
        return os.path.join(s.ARTIFACTS_DIR, self.run_id)

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


# ── Layer 2: Pipeline state (pipeline-level data) ─────────────────


@dataclass
class PipelineState:
    """Pipeline-level state: repository snapshot + decisions + plans.

    Built before renderer selection. Passed to RenderContext.
    This is the SINGLE source of truth for all pipeline-derived data.
    """
    file_nodes: dict = field(default_factory=dict)
    component_nodes: dict = field(default_factory=dict)
    decisions: dict[str, Any] = field(default_factory=dict)
    deletions: list = field(default_factory=list)
    resolved_mapping: dict[str, Any] = field(default_factory=dict)
    exec_ctx: ExecutionContext | None = None


# ── Layer 3: Render context (renderer-level projection) ───────────


@dataclass
class RenderContext:
    """Renderer-level projection of pipeline state.

    The renderer reads ONLY from this context. It does NOT
    access the pipeline directly, index files, or load memory.
    """
    execution: PipelineState | None = None
    feature_flags: dict = field(default_factory=dict)
