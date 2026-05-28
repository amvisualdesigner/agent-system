"""GraphIR Purity Boundary — enforces that filesystem concepts
never leak into the semantic core.

GraphIR (nodes, edges, data, metadata) must remain purely semantic.
No file paths, no workspace references, no worktree concepts.

This module provides:
- A set of blocked keys that must not appear in GraphIR structures
- An enforcement function called at pipeline boundaries
- A custom exception for violations

┌─────────────────────────────────────────────────────────────┐
│               FROZEN SEMANTIC BOUNDARY RULES                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GraphIR NEVER contains:                                    │
│    - file_path, absolute_path, relative_path                │
│    - workspace, worktree                                    │
│    - filesystem state (fs_*, path_*, file_* prefixes)       │
│                                                             │
│  UIComponentNode NEVER contains:                            │
│    - imports, file_path, workspace, worktree                │
│    - repo metadata of any kind                              │
│                                                             │
│  Renderer NEVER:                                            │
│    - reads disk                                             │
│    - knows about git                                        │
│    - resolves worktrees                                     │
│                                                             │
│  ExecutionContext NEVER enters:                             │
│    - GraphIR nodes or metadata                              │
│    - UIComponentNode trees                                  │
│    - semantic metadata of any kind                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import Any

GRAPHIR_BLOCKED_KEYS: set[str] = {
    "file_path", "file_paths", "fs_path", "workspace",
    "worktree", "fs_", "absolute_path", "relative_path",
}

GRAPHIR_BLOCKED_PREFIXES: tuple[str, ...] = ("fs_", "path_", "file_")


class GraphIRBoundaryViolation(Exception):
    """Raised when GraphIR purity is violated by filesystem concepts."""


def enforce_graphir_purity(data: dict[str, Any], context: str = "") -> None:
    """Check that no filesystem concepts leak into GraphIR metadata/data.

    Args:
        data: The dict to check (GraphIRNode.data or GraphIRNode.metadata).
        context: A human-readable label for error messages.

    Raises:
        GraphIRBoundaryViolation: If any blocked key is found.
    """
    for key in data:
        if key in GRAPHIR_BLOCKED_KEYS:
            raise GraphIRBoundaryViolation(
                f"GraphIR purity violation: key '{key}' in {context}. "
                f"GraphIR must not contain filesystem concepts."
            )
        for prefix in GRAPHIR_BLOCKED_PREFIXES:
            if key.startswith(prefix):
                raise GraphIRBoundaryViolation(
                    f"GraphIR purity violation: key '{key}' (prefix '{prefix}') "
                    f"in {context}. Filesystem-prefixed keys are blocked."
                )


def enforce_graph_purity(graph: Any) -> None:
    """Convenience: enforce purity across all nodes in a GraphIR instance.

    Args:
        graph: A GraphIR instance with .nodes dict.

    Raises:
        GraphIRBoundaryViolation: If any node violates purity.
    """
    from app.graphir.models import GraphIR  # noqa: F811
    if not hasattr(graph, "nodes"):
        return
    for node_id, node in graph.nodes.items():
        enforce_graphir_purity(node.data, f"GraphIRNode({node_id}).data")
        enforce_graphir_purity(node.metadata, f"GraphIRNode({node_id}).metadata")
