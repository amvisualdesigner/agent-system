"""Shadow validation — runtime comparison utilities.

Pure: no IO, no REPO_ROOT dependency.

Phase 6a: Added decision-level comparison (shadow_compare_decisions).
Catches semantic divergences that may produce equivalent FileOps
through different decision paths.
"""

from __future__ import annotations

import logging

from app.graphir.models import FileOp
from app.graphir.constraint.models import FileOpDecision

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


def shadow_compare_decisions(
    decisions_legacy: dict[str, FileOpDecision],
    decisions_shadow: dict[str, FileOpDecision],
    run_id: str,
):
    """Compare decisions pre-render to catch semantic divergences.

    Phase 6a: Compares (decision type, target_file) only.
    Does NOT compare confidence or rationale — those can differ
    legitimately across resolvers (e.g., Level 2.5 changes confidence).

    Detects:
    - WRONG_CREATE: extra decision in shadow
    - MISCLASSIFIED_DELETE: missing decision in shadow
    - OPERATION_MISMATCH: same node, different decision or target

    Pure comparison: no side effects.
    """
    all_ids = set(decisions_legacy) | set(decisions_shadow)
    for node_id in sorted(all_ids):
        leg = decisions_legacy.get(node_id)
        shad = decisions_shadow.get(node_id)
        if leg is None:
            logger.warning(
                "SHADOW: extra decision in shadow for %s (run=%s)",
                node_id, run_id,
            )
        elif shad is None:
            logger.warning(
                "SHADOW: missing decision in shadow for %s (run=%s)",
                node_id, run_id,
            )
        elif (leg.decision, leg.target_file) != (shad.decision, shad.target_file):
            logger.warning(
                "SHADOW: decision mismatch for %s — %s/%s vs %s/%s (run=%s)",
                node_id, leg.decision.value, leg.target_file,
                shad.decision.value, shad.target_file, run_id,
            )
