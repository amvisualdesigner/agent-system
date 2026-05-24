"""Confidence Gate — blocks pipeline execution when semantic confidence is too low.

Placed between frame construction and pipeline execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.graphir.semantic_frame import StructuredSemanticFrame

logger = logging.getLogger(__name__)

# Default threshold: calibrated for current decomposition quality
DEFAULT_CONFIDENCE_THRESHOLD = 0.4


@dataclass
class GateResult:
    blocked: bool
    reason: str = ""
    missing_info: list[str] = field(default_factory=list)
    partial_frame: Any = None


def confidence_gate(
    frame: StructuredSemanticFrame,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> GateResult:
    """Evaluate whether the semantic frame has enough confidence to proceed.

    Blocks if:
    1. Confidence below threshold
    2. No actions extracted
    3. No objects extracted
    4. Critical missing info (actions/objects referenced but not resolved)

    Returns GateResult with blocking reason and diagnostic info.
    """
    # 1. Confidence threshold
    if frame.confidence < threshold:
        return GateResult(
            blocked=True,
            reason=(
                f"Semantic confidence {frame.confidence:.3f} "
                f"below threshold {threshold}. "
                "The system could not reliably understand the task request."
            ),
            missing_info=frame.missing_info,
            partial_frame=frame,
        )

    # 2. No actions
    if not frame.actions:
        return GateResult(
            blocked=True,
            reason=(
                "No actions detected in task. "
                "The system needs at least one actionable verb "
                "(e.g., create, modify, add, override)."
            ),
            missing_info=frame.missing_info + ["no_action_detected"],
            partial_frame=frame,
        )

    # 3. No objects
    if not frame.objects:
        return GateResult(
            blocked=True,
            reason=(
                "No objects detected in task. "
                "The system needs at least one target component "
                "(e.g., table, chart, dashboard, KPI row)."
            ),
            missing_info=frame.missing_info + ["no_object_detected"],
            partial_frame=frame,
        )

    # Passed all gates
    return GateResult(blocked=False)
