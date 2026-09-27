"""Workspace snapshot for pre-apply concurrency detection.

Single materialization flow (F4 concurrency, option C):
  - /agent/confirm captures the physical state of the worktree the confirmed
    plan/preview is based on (confirm_snapshot) and persists it.
  - /agent/apply recomputes the same state and refuses to materialize
    (NO WRITE, zero FileOps applied) if anything changed in between.

The snapshot is a pure physical fingerprint (rel_path -> (mtime_ns, size)).
It introduces no authority, no merge, no reinterpretation: changed state
means the confirmed plan/preview is stale, and the run must be restarted.
"""

from __future__ import annotations

import os

IGNORED_DIRS = frozenset({".git", "__pycache__", "node_modules"})


def snapshot_worktree(workspace: str) -> dict[str, list[int]]:
    """Fingerprint every file under the workspace.

    Returns rel_path -> [mtime_ns, size] (list form so it round-trips
    through JSON persistence in run state).
    """
    result: dict[str, list[int]] = {}
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for fn in files:
            rel = os.path.relpath(os.path.join(root, fn), workspace)
            st = os.lstat(os.path.join(root, fn))
            result[rel] = [st.st_mtime_ns, st.st_size]
    return result


def diff_snapshots(before: dict, after: dict) -> list[str]:
    """Return the sorted list of paths that differ between two snapshots."""
    changed: list[str] = []
    for rel in sorted(set(before) | set(after)):
        if before.get(rel) != after.get(rel):
            changed.append(rel)
    return changed