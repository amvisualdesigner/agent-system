"""StructuralDiffEngine + BoundaryValidator — unit tests.

Pure Core: zero IO, 100% deterministic.
Tests surgical line-range merge, fallback paths, and boundary
validation edge cases.
"""

from app.graphir.constraint.models import Decision, ComponentBoundary
from app.graphir.constraint.diff import (
    StructuralDiffEngine,
    BoundaryValidator,
    BoundaryHealth,
    EditOperation,
    ExtendStrategy,
)
from app.graphir.constraint.executor import FileOpExecutor


def _boundary(
    name: str = "KpiRow",
    start: int = 2,
    end: int = 5,
    kind: str = "component",
) -> ComponentBoundary:
    return ComponentBoundary(
        name=name,
        kind=kind,
        line_start=start,
        line_end=end,
        is_named_export=True,
        export_statement=f"export const {name} = ...",
    )


# ── BoundaryValidator tests ────────────────────────────────────────

class TestBoundaryValidator:
    """Boundary health checks against actual file state."""

    def test_valid_boundary(self):
        lines = ["line1", "line2", "line3", "line4", "line5"]
        b = _boundary("KpiRow", start=2, end=4)
        health = BoundaryValidator.validate(b, lines, [b])
        assert health.is_valid
        assert health.confidence == 0.7  # regex default
        assert health.boundary_line_start == 2
        assert health.boundary_line_end == 4

    def test_ast_extraction_higher_confidence(self):
        lines = ["a", "b", "c"]
        b = _boundary("KpiRow", start=1, end=3)
        health = BoundaryValidator.validate(b, lines, [b], extraction_method="ast")
        assert health.is_valid
        assert health.confidence == 1.0

    def test_boundary_none_is_invalid(self):
        health = BoundaryValidator.validate(None, [], [])
        assert not health.is_valid
        assert health.confidence == 0.0

    def test_line_start_zero_fails(self):
        lines = ["a", "b"]
        b = _boundary("X", start=0, end=2)
        health = BoundaryValidator.validate(b, lines, [b])
        assert not health.is_valid

    def test_line_start_negative_fails(self):
        lines = ["a", "b"]
        b = _boundary("X", start=-1, end=2)
        health = BoundaryValidator.validate(b, lines, [b])
        assert not health.is_valid

    def test_line_end_less_than_start_fails(self):
        lines = ["a", "b", "c"]
        b = _boundary("X", start=3, end=1)
        health = BoundaryValidator.validate(b, lines, [b])
        assert not health.is_valid

    def test_line_start_beyond_file_fails(self):
        lines = ["a", "b"]
        b = _boundary("X", start=5, end=6)
        health = BoundaryValidator.validate(b, lines, [b])
        assert not health.is_valid

    def test_line_end_beyond_file_fails(self):
        lines = ["a", "b"]
        b = _boundary("X", start=1, end=10)
        health = BoundaryValidator.validate(b, lines, [b])
        assert not health.is_valid

    def test_overlapping_boundaries_detected(self):
        lines = ["a"] * 10
        b1 = _boundary("A", start=2, end=5)
        b2 = _boundary("B", start=4, end=7)
        health = BoundaryValidator.validate(b1, lines, [b1, b2])
        assert health.is_valid  # overlaps are warnings, not failures
        assert "B" in health.overlapping_boundaries


# ── StructuralDiffEngine tests ─────────────────────────────────────

class TestStructuralDiffEngine:
    """Edit computation for all decision types."""

    def test_create_decision(self):
        edit = StructuralDiffEngine.compute_edit(
            "<content>", "src/KpiRow.tsx", [],
            None, Decision.CREATE,
        )
        assert edit.action == "create"
        assert edit.source_file == "src/KpiRow.tsx"
        assert edit.content == "<content>"

    def test_split_decision(self):
        edit = StructuralDiffEngine.compute_edit(
            "<content>", "src/KpiRow.tsx", [],
            None, Decision.SPLIT,
        )
        assert edit.action == "create"

    def test_update_with_valid_boundary_replace_range(self):
        lines = ["import", "export const KpiRow = () => {", "  return null;", "}", ""]
        b = _boundary("KpiRow", start=2, end=4)
        edit = StructuralDiffEngine.compute_edit(
            "export const KpiRow = () => <div>new</div>;",
            "src/KpiRow.tsx", lines, b, Decision.UPDATE,
        )
        assert edit.action == "replace_range"
        assert edit.range_start == 2
        assert edit.range_end == 4

    def test_update_without_boundary_falls_back(self):
        lines = ["a", "b", "c"]
        edit = StructuralDiffEngine.compute_edit(
            "full file", "src/F.tsx", lines,
            None, Decision.UPDATE,
        )
        assert edit.action == "replace_file"

    def test_update_with_boundary_out_of_range_falls_back(self):
        lines = ["a"]
        b = _boundary("X", start=5, end=10)
        edit = StructuralDiffEngine.compute_edit(
            "full", "src/F.tsx", lines,
            b, Decision.UPDATE,
        )
        assert edit.action == "replace_file"

    def test_extend_append_region_defaults_replace_file(self):
        lines = ["a", "b", "c"]
        edit = StructuralDiffEngine.compute_edit(
            "new content", "src/F.tsx", lines,
            None, Decision.EXTEND,
            extend_strategy=ExtendStrategy.REPLACE_FILE,
        )
        assert edit.action == "replace_file"

    def test_extend_append_region_inserts_after_last_boundary(self):
        lines = ["a", "b", "c", "d", "e"]
        boundaries = [
            _boundary("Existing", start=1, end=2),
            _boundary("Target", start=3, end=4),
        ]
        edit = StructuralDiffEngine.compute_edit(
            "<new>", "src/F.tsx", lines,
            boundaries[1], Decision.EXTEND,
            all_boundaries=boundaries,
            extend_strategy=ExtendStrategy.APPEND_REGION,
        )
        assert edit.action == "insert_range"
        # Last boundary ends at 4, so insert at 5
        assert edit.range_start == 5
        assert edit.content == "<new>"

    def test_extend_append_region_no_boundaries_inserts_at_eof(self):
        lines = ["a", "b"]
        edit = StructuralDiffEngine.compute_edit(
            "<new>", "src/F.tsx", lines,
            None, Decision.EXTEND,
            all_boundaries=[],
            extend_strategy=ExtendStrategy.APPEND_REGION,
        )
        assert edit.action == "insert_range"
        # No boundaries → insert at EOF (3)
        assert edit.range_start == 3

    def test_extend_append_region_single_boundary(self):
        lines = ["a", "b", "c"]
        b = _boundary("Only", start=1, end=2)
        edit = StructuralDiffEngine.compute_edit(
            "<new>", "src/F.tsx", lines,
            b, Decision.EXTEND,
            all_boundaries=[b],
            extend_strategy=ExtendStrategy.APPEND_REGION,
        )
        assert edit.action == "insert_range"
        assert edit.range_start == 3  # after boundary end (2)


# ── Surgical replacement tests (renderer internals) ────────────────

class TestSurgicalApply:
    """_apply_surgical — test the line-range merge directly."""

    def test_replace_range_mid_file(self):
        existing = "line1\nline2\nline3\nline4\n"
        edit = EditOperation(
            action="replace_range",
            source_file="t.tsx",
            range_start=2,
            range_end=3,
            content="REPLACED",
        )
        result = FileOpExecutor._apply_surgical(existing, edit)
        expected = "line1\nREPLACED\nline4\n"
        assert result == expected

    def test_insert_range_at_line(self):
        existing = "line1\nline2\n"
        edit = EditOperation(
            action="insert_range",
            source_file="t.tsx",
            range_start=3,
            range_end=2,
            content="INSERTED",
        )
        result = FileOpExecutor._apply_surgical(existing, edit)
        expected = "line1\nline2\nINSERTED\n"
        assert result == expected

    def test_replace_first_lines(self):
        existing = "old1\nold2\nkeep\n"
        edit = EditOperation(
            action="replace_range",
            source_file="t.tsx",
            range_start=1,
            range_end=2,
            content="new1\nnew2",
        )
        result = FileOpExecutor._apply_surgical(existing, edit)
        expected = "new1\nnew2\nkeep\n"
        assert result == expected

    def test_replace_last_lines(self):
        existing = "keep\nold1\nold2\n"
        edit = EditOperation(
            action="replace_range",
            source_file="t.tsx",
            range_start=2,
            range_end=3,
            content="new1\nnew2",
        )
        result = FileOpExecutor._apply_surgical(existing, edit)
        expected = "keep\nnew1\nnew2\n"
        assert result == expected

    def test_empty_file_insert(self):
        result = FileOpExecutor._apply_surgical("", EditOperation(
            action="replace_range",
            source_file="t.tsx",
            range_start=1,
            range_end=1,
            content="content",
        ))
        # Empty file has no trailing newline → result also has none
        assert result == "content"
