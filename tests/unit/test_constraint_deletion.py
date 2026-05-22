"""Phase 6a — unit tests for DELETE detection and structural deletion.

Pure Core: zero IO, 100% deterministic tests.
"""

from app.graphir.constraint.models import (
    Decision, FileOpDecision, FileNode, MemoryRecord, DeletionRecord,
    ComponentBoundary,
)
from app.graphir.constraint.identity import CanonicalIdentity
from app.graphir.constraint.deletion import detect_deletions
from app.graphir.constraint.diff import StructuralDiffEngine, EditOperation


# ── Helpers ────────────────────────────────────────────────────────────

def _memory_record(fp: str, file_path: str, comp_name: str = "KpiRow") -> MemoryRecord:
    return MemoryRecord(fingerprint=fp, file_path=file_path, component_name=comp_name)


def _identity(comp_name="KpiRow", cap="presentation.kpi_row",
              domain=("generic",), params_hash="abc") -> CanonicalIdentity:
    return CanonicalIdentity(
        component_name=comp_name,
        capability_id=cap,
        domain=domain,
        params_hash=params_hash,
    )


def _file_node(path: str, comp_names=None, boundaries=None,
               canonical_ids=None) -> FileNode:
    return FileNode(
        id=f"file:{path}",
        node_type="file",
        path=path,
        component_names=comp_names or [],
        component_boundaries=boundaries or [],
        canonical_ids=canonical_ids or [],
    )


def _boundary(name: str, start: int, end: int) -> ComponentBoundary:
    return ComponentBoundary(name=name, kind="component",
                             line_start=start, line_end=end)


# ── detect_deletions ──────────────────────────────────────────────────

class TestDetectDeletions:
    """detect_deletions: state-diff based DELETE detection."""

    def test_no_delete_when_fingerprint_active(self):
        """Memory fingerprint is in active identities → no deletion."""
        identity = _identity()
        fp = identity.fingerprint()
        memory = {fp: _memory_record(fp, "src/KpiRow.tsx")}
        file_nodes = {"src/KpiRow.tsx": _file_node("src/KpiRow.tsx")}
        result = detect_deletions(memory, {"n0": identity}, file_nodes)
        assert result == []

    def test_no_delete_when_file_missing(self):
        """Memory fingerprint exists but file is gone → no deletion."""
        identity = _identity(comp_name="Deleted")
        fp = identity.fingerprint()
        memory = {fp: _memory_record(fp, "src/Deleted.tsx", comp_name="Deleted")}
        deletions = detect_deletions(memory, {}, {})
        assert deletions == []

    def test_delete_when_all_conditions_met(self):
        """Memory fingerprint exists, not active, file still in repo → DELETE."""
        identity = _identity(comp_name="OldComp")
        fp = identity.fingerprint()
        memory = {fp: _memory_record(fp, "src/OldComp.tsx", comp_name="OldComp")}
        file_nodes = {"src/OldComp.tsx": _file_node("src/OldComp.tsx")}
        deletions = detect_deletions(memory, {}, file_nodes)
        assert len(deletions) == 1
        assert deletions[0].fingerprint == fp
        assert deletions[0].file_path == "src/OldComp.tsx"
        assert deletions[0].component_name == "OldComp"

    def test_multiple_deletions(self):
        """Two stale memory entries → two DeletionRecords."""
        fp1 = "fp:comp1:abc"
        fp2 = "fp:comp2:def"
        memory = {
            fp1: _memory_record(fp1, "src/Comp1.tsx", "Comp1"),
            fp2: _memory_record(fp2, "src/Comp2.tsx", "Comp2"),
        }
        file_nodes = {
            "src/Comp1.tsx": _file_node("src/Comp1.tsx"),
            "src/Comp2.tsx": _file_node("src/Comp2.tsx"),
        }
        deletions = detect_deletions(memory, {}, file_nodes)
        assert len(deletions) == 2

    def test_mixed_active_and_stale(self):
        """Active entries skip, stale entries produce DELETE."""
        active_identity = _identity(comp_name="Active")
        active_fp = active_identity.fingerprint()
        stale_fp = "fp:stale:abc"

        memory = {
            active_fp: _memory_record(active_fp, "src/Active.tsx", "Active"),
            stale_fp: _memory_record(stale_fp, "src/Stale.tsx", "Stale"),
        }
        file_nodes = {
            "src/Active.tsx": _file_node("src/Active.tsx"),
            "src/Stale.tsx": _file_node("src/Stale.tsx"),
        }
        deletions = detect_deletions(memory, {"n0": active_identity}, file_nodes)
        assert len(deletions) == 1
        assert deletions[0].fingerprint == stale_fp
        assert deletions[0].file_path == "src/Stale.tsx"
        assert deletions[0].component_name == "Stale"

    def test_empty_memory_no_deletions(self):
        assert detect_deletions({}, {}, {}) == []


# ── compute_delete_edit ───────────────────────────────────────────────

class TestComputeDeleteEdit:
    """StructuralDiffEngine.compute_delete_edit structural DELETE."""

    EXISTING_CONTENT = [
        "import React from 'react';",
        "",
        "export const KpiRow = () => null;",
        "",
        "export const Chart = () => null;",
    ]

    def test_replace_range_with_boundary(self):
        """Known boundary → replace_range with empty content."""
        boundary = _boundary("KpiRow", 3, 3)
        edit = StructuralDiffEngine.compute_delete_edit(
            "src/Chart.tsx", self.EXISTING_CONTENT, boundary,
        )
        assert edit is not None
        assert edit.action == "replace_range"
        assert edit.source_file == "src/Chart.tsx"
        assert edit.range_start == 3
        assert edit.range_end == 3
        assert edit.content == ""

    def test_delete_file_when_allowed(self):
        """No boundary + allow_full_delete=True → delete_file."""
        edit = StructuralDiffEngine.compute_delete_edit(
            "src/KpiRow.tsx", self.EXISTING_CONTENT, None,
            allow_full_delete=True,
        )
        assert edit is not None
        assert edit.action == "delete_file"
        assert edit.source_file == "src/KpiRow.tsx"

    def test_none_when_no_boundary_and_not_allowed(self):
        """No boundary + allow_full_delete=False → None (skip)."""
        edit = StructuralDiffEngine.compute_delete_edit(
            "src/Chart.tsx", self.EXISTING_CONTENT, None,
            allow_full_delete=False,
        )
        assert edit is None

    def test_replace_range_over_multiple_lines(self):
        """Multi-line boundary replaced with empty content."""
        boundary = _boundary("KpiRow", 2, 4)
        lines = [
            "import React from 'react';",
            "export const KpiRow = () => {",
            "  return <div>Hello</div>;",
            "};",
            "",
            "export const Chart = () => null;",
        ]
        edit = StructuralDiffEngine.compute_delete_edit(
            "src/Chart.tsx", lines, boundary,
        )
        assert edit is not None
        assert edit.action == "replace_range"
        assert edit.range_start == 2
        assert edit.range_end == 4
        assert edit.content == ""

    def test_invalid_boundary_returns_none(self):
        """Boundary outside file range → None."""
        boundary = _boundary("KpiRow", 100, 110)
        edit = StructuralDiffEngine.compute_delete_edit(
            "src/Chart.tsx", self.EXISTING_CONTENT, boundary,
        )
        # Falls through to check allow_full_delete, which defaults False
        assert edit is None

    def test_invalid_boundary_with_allow_full_delete(self):
        """Invalid boundary + allow_full_delete → delete_file."""
        boundary = _boundary("KpiRow", 100, 110)
        edit = StructuralDiffEngine.compute_delete_edit(
            "src/KpiRow.tsx", self.EXISTING_CONTENT, boundary,
            allow_full_delete=True,
        )
        assert edit is not None
        assert edit.action == "delete_file"


# ── IdentityResolver Level 2.5 ────────────────────────────────────────

class TestResolverLevel25:
    """Level 2.5 existence-aware resolver prevents duplicate CREATE."""

    def test_component_name_exists_in_file_node(self):
        """Component name in file_node.component_names → UPDATE."""
        from app.graphir.constraint.resolver import IdentityResolver

        identity = _identity(comp_name="KpiRow")
        file_nodes = {
            "src/Components.tsx": _file_node(
                "src/Components.tsx",
                comp_names=["KpiRow", "Chart"],
                canonical_ids=[],
            ),
        }
        resolver = IdentityResolver()
        decisions = resolver.resolve(
            {"n0": identity},
            {"n0": [(0.20, file_nodes["src/Components.tsx"])]},
            file_nodes,
        )
        assert decisions["n0"].decision == Decision.UPDATE
        assert decisions["n0"].target_file == "src/Components.tsx"
        assert decisions["n0"].confidence == 0.85

    def test_level_2_5_takes_priority_over_scoring(self):
        """Level 2.5 fires before Level 3 scoring despite low score."""
        from app.graphir.constraint.resolver import IdentityResolver

        identity = _identity(comp_name="KpiRow")
        file_nodes = {
            "src/Components.tsx": _file_node(
                "src/Components.tsx",
                comp_names=["KpiRow"],
                canonical_ids=[],
            ),
            "src/Other.tsx": _file_node("src/Other.tsx"),
        }
        resolver = IdentityResolver()
        # Score is low enough for CREATE, but Level 2.5 intercepts
        decisions = resolver.resolve(
            {"n0": identity},
            {"n0": [(0.10, file_nodes["src/Other.tsx"])]},
            file_nodes,
        )
        assert decisions["n0"].decision == Decision.UPDATE
        assert decisions["n0"].target_file == "src/Components.tsx"

    def test_level_2_5_still_allows_create_for_new_component(self):
        """Component not in any file_node → falls through to Level 3."""
        from app.graphir.constraint.resolver import IdentityResolver

        identity = _identity(comp_name="BrandNew")
        file_nodes = {
            "src/Existing.tsx": _file_node(
                "src/Existing.tsx",
                comp_names=["KpiRow"],
            ),
        }
        resolver = IdentityResolver()
        decisions = resolver.resolve(
            {"n0": identity},
            {"n0": [(0.20, file_nodes["src/Existing.tsx"])]},
            file_nodes,
        )
        # Level 2.5 doesn't match "BrandNew" → falls to scoring → CREATE
        assert decisions["n0"].decision == Decision.CREATE


# ── Executor delete_file action ───────────────────────────────────────

class TestExecutorDeleteFile:
    """FileOpExecutor handles delete_file EditOperation."""

    def test_delete_file_produces_delete_fileop(self):
        from app.graphir.constraint.executor import FileOpExecutor
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            edit = EditOperation(action="delete_file", source_file="src/ToDelete.tsx")
            executor = FileOpExecutor(tmp)
            fileops = executor.execute(edit)
            assert len(fileops) == 1
            assert fileops[0].action == "delete"
            assert fileops[0].path == "src/ToDelete.tsx"
            assert fileops[0].content == ""


# ── Memory merge with deleted_fingerprints ────────────────────────────

class TestMemoryMergeDeletions:
    """RepositorySemanticMemory.merge handles deleted_fingerprints."""

    def test_deleted_fingerprint_removed(self):
        from app.graphir.constraint.memory import RepositorySemanticMemory

        fp = "fp:comp:abc"
        existing = {
            fp: _memory_record(fp, "src/Comp.tsx", "Comp"),
        }
        merged = RepositorySemanticMemory.merge(
            {}, {}, existing,
            deleted_fingerprints={fp},
        )
        assert fp not in merged

    def test_only_specified_fingerprints_removed(self):
        from app.graphir.constraint.memory import RepositorySemanticMemory

        fp_keep = "fp:keep:abc"
        fp_delete = "fp:delete:def"
        existing = {
            fp_keep: _memory_record(fp_keep, "src/Keep.tsx", "Keep"),
            fp_delete: _memory_record(fp_delete, "src/Delete.tsx", "Delete"),
        }
        merged = RepositorySemanticMemory.merge(
            {}, {}, existing,
            deleted_fingerprints={fp_delete},
        )
        assert fp_keep in merged
        assert fp_delete not in merged

    def test_no_deleted_fingerprints_no_change(self):
        from app.graphir.constraint.memory import RepositorySemanticMemory

        fp = "fp:comp:abc"
        existing = {
            fp: _memory_record(fp, "src/Comp.tsx", "Comp"),
        }
        merged = RepositorySemanticMemory.merge({}, {}, existing)
        assert fp in merged
        assert merged[fp].file_path == "src/Comp.tsx"
