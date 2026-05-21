"""Shadow validation — runtime comparison utilities.

Pure: no IO, no REPO_ROOT dependency.
"""

from __future__ import annotations

import logging

from app.graphir.models import FileOp

logger = logging.getLogger(__name__)


def shadow_compare(fileops_legacy: list[FileOp], fileops_shadow: list[FileOp], run_id: str):
    """Compare legacy vs shadow FileOps, log discrepancies.

    Pure comparison: no side effects, no state modification.
    Logs at WARNING level for any divergence.
    """
    if len(fileops_legacy) != len(fileops_shadow):
        logger.warning(
            "SHADOW: FileOp count mismatch — legacy=%d shadow=%d (run=%s)",
            len(fileops_legacy), len(fileops_shadow), run_id,
        )

    for i, (legacy, shadow) in enumerate(zip(fileops_legacy, fileops_shadow)):
        diffs: list[str] = []
        if legacy.action != shadow.action:
            diffs.append(f"action: {legacy.action} vs {shadow.action}")
        if legacy.path != shadow.path:
            diffs.append(f"path: {legacy.path} vs {shadow.path}")
        if legacy.content != shadow.content:
            diffs.append(f"content: {len(legacy.content)}b vs {len(shadow.content)}b")

        if diffs:
            logger.warning(
                "SHADOW: FileOp[%d] differs — %s (run=%s)",
                i, "; ".join(diffs), run_id,
            )
