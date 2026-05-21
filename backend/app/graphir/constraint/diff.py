"""StructuralDiffEngine — line-range merge via ComponentBoundary.

Pure Core: zero IO, 100% deterministic.

Transforms generated content + ComponentBoundary into surgical
EditOperations that replace only the target range instead of the
entire file. Falls back to full-file replacement when boundaries
are unavailable or invalid.

Design rules:
- Every boundary is validated BEFORE the diff engine sees it.
- Boundary is always the index-time snapshot; FileOpExecutor
  re-reads the file at apply time for line-count validation.
- EXTEND uses ExtendStrategy enum: generators opt into
  APPEND_REGION; default is REPLACE_FILE (backward compatible).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.graphir.constraint.models import Decision, ComponentBoundary

logger = logging.getLogger(__name__)


# ── EditOperation ──────────────────────────────────────────────────

@dataclass
class EditOperation:
    """A single surgical edit targeting a file range.

    action: "create" | "replace_range" | "insert_range" | "replace_file"
    source_file: target file path
    range_start: 1-indexed, inclusive (0 for create/replace_file)
    range_end: 1-indexed, inclusive (0 for create/replace_file)
    content: generated content to apply
    """
    action: str
    source_file: str
    range_start: int = 0
    range_end: int = 0
    content: str = ""


# ── ExtendStrategy ─────────────────────────────────────────────────

class ExtendStrategy(Enum):
    """Controls how EXTEND decisions are applied.

    REPLACE_FILE: legacy behavior — full file rewrite (default).
    APPEND_REGION: Phase 5 behavior — append after last boundary.
    """
    REPLACE_FILE = "replace_file"
    APPEND_REGION = "append_region"


# ── BoundaryHealth ─────────────────────────────────────────────────

@dataclass
class BoundaryHealth:
    """Result of validating a boundary against actual file state."""
    is_valid: bool
    existing_lines: int = 0
    boundary_line_start: int = 0
    boundary_line_end: int = 0
    overlapping_boundaries: list[str] = field(default_factory=list)
    confidence: float = 0.0  # 1.0 = AST, 0.7 = regex, 0.0 = invalid


# ── BoundaryValidator ──────────────────────────────────────────────

class BoundaryValidator:
    """Pure Core: validates ComponentBoundary against actual file state.

    Runs BEFORE StructuralDiffEngine. When validation fails, the
    caller falls back to full-file replacement.
    """

    @staticmethod
    def validate(
        boundary: ComponentBoundary | None,
        existing_lines: list[str],
        all_boundaries: list[ComponentBoundary],
        extraction_method: str = "regex",
    ) -> BoundaryHealth:
        """Check boundary against current file state.

        Args:
            boundary: The target boundary (may be None for CREATE).
            existing_lines: File content split by newline at apply time.
            all_boundaries: All boundaries in the file (for overlap check).
            extraction_method: "ast" or "regex".

        Returns:
            BoundaryHealth with is_valid=False if any check fails.
        """
        if boundary is None:
            return BoundaryHealth(
                is_valid=False, existing_lines=len(existing_lines),
                confidence=0.0,
            )

        line_count = len(existing_lines)
        ls, le = boundary.line_start, boundary.line_end

        # Range validation
        if ls <= 0:
            logger.warning("Boundary %s: line_start=%d <= 0", boundary.name, ls)
            return BoundaryHealth(
                is_valid=False, existing_lines=line_count,
                boundary_line_start=ls, boundary_line_end=le,
                confidence=0.0,
            )
        if le < ls:
            logger.warning("Boundary %s: line_end=%d < line_start=%d", boundary.name, le, ls)
            return BoundaryHealth(
                is_valid=False, existing_lines=line_count,
                boundary_line_start=ls, boundary_line_end=le,
                confidence=0.0,
            )
        if ls > line_count:
            logger.warning(
                "Boundary %s: line_start=%d > file_lines=%d (stale index)",
                boundary.name, ls, line_count,
            )
            return BoundaryHealth(
                is_valid=False, existing_lines=line_count,
                boundary_line_start=ls, boundary_line_end=le,
                confidence=0.0,
            )
        if le > line_count:
            logger.warning(
                "Boundary %s: line_end=%d > file_lines=%d (stale index, truncated)",
                boundary.name, le, line_count,
            )
            return BoundaryHealth(
                is_valid=False, existing_lines=line_count,
                boundary_line_start=ls, boundary_line_end=le,
                confidence=0.0,
            )

        # Overlap check
        overlapping: list[str] = []
        for other in all_boundaries:
            if other.name == boundary.name:
                continue
            if other.line_start <= le and other.line_end >= ls:
                overlapping.append(other.name)

        confidence = 1.0 if extraction_method == "ast" else 0.7

        return BoundaryHealth(
            is_valid=True,
            existing_lines=line_count,
            boundary_line_start=ls,
            boundary_line_end=le,
            overlapping_boundaries=overlapping,
            confidence=confidence,
        )


# ── StructuralDiffEngine ──────────────────────────────────────────

class StructuralDiffEngine:
    """Pure Core: computes EditOperation from intent + boundary.

    No IO, no file awareness. All file state is passed in as
    existing_lines.
    """

    @staticmethod
    def compute_edit(
        generated_content: str,
        target_file_path: str,
        existing_lines: list[str],
        component_boundary: ComponentBoundary | None,
        decision: Decision,
        all_boundaries: list[ComponentBoundary] | None = None,
        extend_strategy: ExtendStrategy = ExtendStrategy.REPLACE_FILE,
    ) -> EditOperation:
        """Compute minimal edit to apply intent to file.

        Args:
            generated_content: Content from ContentGenerator.
            target_file_path: Decision target file.
            existing_lines: Lines of existing file (empty for CREATE).
            component_boundary: Boundary of target component (None for CREATE).
            decision: Decision type.
            all_boundaries: All boundaries in the file (used for EXTEND
                to find the last boundary to append after).
            extend_strategy: Controls EXTEND behavior.

        Returns:
            EditOperation with surgical range when possible,
            full-file replacement as fallback.
        """
        if decision in (Decision.CREATE, Decision.SPLIT):
            return EditOperation(
                action="create",
                source_file=target_file_path,
                content=generated_content,
            )

        if decision == Decision.EXTEND and extend_strategy == ExtendStrategy.APPEND_REGION:
            return StructuralDiffEngine._append_after_last_boundary(
                generated_content, target_file_path, existing_lines,
                all_boundaries or [],
            )

        # UPDATE or EXTEND (REPLACE_FILE): surgical replace if boundary available
        if component_boundary is not None:
            ls, le = component_boundary.line_start, component_boundary.line_end
            line_count = len(existing_lines)

            if 1 <= ls <= le <= line_count:
                return EditOperation(
                    action="replace_range",
                    source_file=target_file_path,
                    range_start=ls,
                    range_end=le,
                    content=generated_content,
                )

        # Fallback: full-file replacement
        return EditOperation(
            action="replace_file",
            source_file=target_file_path,
            content=generated_content,
        )

    @staticmethod
    def _append_after_last_boundary(
        generated_content: str,
        target_file_path: str,
        existing_lines: list[str],
        all_boundaries: list[ComponentBoundary],
    ) -> EditOperation:
        """Append content after the last boundary in the file."""
        if not all_boundaries:
            insert_line = len(existing_lines) + 1
        else:
            last_end = max(b.line_end for b in all_boundaries)
            insert_line = min(last_end + 1, len(existing_lines) + 1)

        return EditOperation(
            action="insert_range",
            source_file=target_file_path,
            range_start=insert_line,
            range_end=insert_line - 1,
            content=generated_content,
        )
