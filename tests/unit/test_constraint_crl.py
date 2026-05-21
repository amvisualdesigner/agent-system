"""ConflictResolutionLayer — unit tests for memory reconciliation.

Pure Core: zero IO, 100% deterministic.
Tests that CRL correctly detects and resolves: STALE_MAPPING,
MISSING_TARGET, DUPLICATE_BINDING.
"""

from app.graphir.constraint.crl import ConflictResolutionLayer
from app.graphir.constraint.models import (
    ConflictType,
    ResolutionStrategy,
    FileNode,
)


def _file(path="src/KpiRow.tsx", exports=None, comp_names=None) -> FileNode:
    return FileNode(
        id=f"file:{path}",
        node_type="file",
        path=path,
        exports=list(exports) if exports is not None else ["KpiRow"],
        component_names=list(comp_names) if comp_names is not None else ["KpiRow"],
    )


class TestCRLStaleMapping:
    """STALE_MAPPING: fingerprint → file no longer in file_nodes."""

    def test_stale_mapping_removed(self):
        crl = ConflictResolutionLayer()
        memory = {"fp:abc": "src/Gone.tsx"}
        cleaned, conflicts = crl.resolve(memory, {})

        assert "fp:abc" not in cleaned
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == ConflictType.STALE_MAPPING
        assert conflicts[0].resolution == ResolutionStrategy.INVALIDATE

    def test_multiple_stale_all_removed(self):
        crl = ConflictResolutionLayer()
        memory = {
            "fp:1": "src/A.tsx",
            "fp:2": "src/B.tsx",
        }
        cleaned, conflicts = crl.resolve(memory, {})

        assert cleaned == {}
        assert len(conflicts) == 2
        for c in conflicts:
            assert c.conflict_type == ConflictType.STALE_MAPPING

    def test_valid_mapping_survives(self):
        crl = ConflictResolutionLayer()
        memory = {"fp:abc": "src/KpiRow.tsx"}
        files = {"src/KpiRow.tsx": _file("src/KpiRow.tsx")}
        cleaned, conflicts = crl.resolve(memory, files)

        assert cleaned == memory
        assert len(conflicts) == 0

    def test_mixed_valid_and_stale(self):
        crl = ConflictResolutionLayer()
        memory = {
            "fp:valid": "src/KpiRow.tsx",
            "fp:stale": "src/Gone.tsx",
        }
        files = {"src/KpiRow.tsx": _file("src/KpiRow.tsx")}
        cleaned, conflicts = crl.resolve(memory, files)

        assert "fp:valid" in cleaned
        assert "fp:stale" not in cleaned
        assert len(conflicts) == 1

    def test_stale_generates_conflict_record(self):
        crl = ConflictResolutionLayer()
        memory = {"fp:gone": "src/Deleted.tsx"}
        cleaned, conflicts = crl.resolve(memory, {})

        c = conflicts[0]
        assert c.identity_fingerprint == "fp:gone"
        assert c.expected_file == "src/Deleted.tsx"
        assert c.severity == 0.8
        assert c.metadata == {"reason": "file_not_found"}


class TestCRLMissingTarget:
    """MISSING_TARGET: file exists but has no exports/components."""

    def test_missing_target_removed(self):
        crl = ConflictResolutionLayer()
        memory = {"fp:abc": "src/Empty.tsx"}
        files = {"src/Empty.tsx": _file("src/Empty.tsx", exports=[], comp_names=[])}
        cleaned, conflicts = crl.resolve(memory, files)

        assert "fp:abc" not in cleaned
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == ConflictType.MISSING_TARGET

    def test_file_with_exports_survives(self):
        crl = ConflictResolutionLayer()
        memory = {"fp:abc": "src/KpiRow.tsx"}
        files = {"src/KpiRow.tsx": _file("src/KpiRow.tsx", exports=["KpiRow"])}
        cleaned, conflicts = crl.resolve(memory, files)

        assert "fp:abc" in cleaned
        assert len(conflicts) == 0

    def test_file_with_components_survives(self):
        crl = ConflictResolutionLayer()
        memory = {"fp:abc": "src/KpiRow.tsx"}
        files = {"src/KpiRow.tsx": _file(
            "src/KpiRow.tsx", exports=[], comp_names=["KpiRow"],
        )}
        cleaned, conflicts = crl.resolve(memory, files)

        assert "fp:abc" in cleaned

    def test_empty_exports_no_components_removed(self):
        crl = ConflictResolutionLayer()
        memory = {"fp:abc": "src/TsxOnly.tsx"}
        files = {"src/TsxOnly.tsx": _file(
            "src/TsxOnly.tsx", exports=[], comp_names=[],
        )}
        cleaned, conflicts = crl.resolve(memory, files)

        assert "fp:abc" not in cleaned


class TestCRLDuplicateBinding:
    """DUPLICATE_BINDING: two fingerprints → same path."""

    def test_duplicate_binding_detected(self):
        crl = ConflictResolutionLayer()
        memory = {
            "fp:one": "src/KpiRow.tsx",
            "fp:two": "src/KpiRow.tsx",
        }
        files = {"src/KpiRow.tsx": _file("src/KpiRow.tsx")}
        cleaned, conflicts = crl.resolve(memory, files)

        # Both kept (KEEP resolution)
        assert "fp:one" in cleaned
        assert "fp:two" in cleaned
        # Two conflict records (one per fingerprint)
        dupes = [c for c in conflicts if c.conflict_type == ConflictType.DUPLICATE_BINDING]
        assert len(dupes) == 2
        for d in dupes:
            assert d.resolution == ResolutionStrategy.KEEP

    def test_duplicate_with_stale(self):
        """Duplicates detected only among surviving mappings."""
        crl = ConflictResolutionLayer()
        memory = {
            "fp:one": "src/KpiRow.tsx",
            "fp:two": "src/KpiRow.tsx",
            "fp:stale": "src/Gone.tsx",
        }
        files = {"src/KpiRow.tsx": _file("src/KpiRow.tsx")}
        cleaned, conflicts = crl.resolve(memory, files)

        assert "fp:one" in cleaned
        assert "fp:two" in cleaned
        assert "fp:stale" not in cleaned

        stale = [c for c in conflicts if c.conflict_type == ConflictType.STALE_MAPPING]
        dupes = [c for c in conflicts if c.conflict_type == ConflictType.DUPLICATE_BINDING]
        assert len(stale) == 1
        assert len(dupes) == 2


class TestCRLEdgeCases:
    """Edge cases and empty inputs."""

    def test_empty_memory(self):
        crl = ConflictResolutionLayer()
        cleaned, conflicts = crl.resolve({}, {"src/F.tsx": _file("src/F.tsx")})
        assert cleaned == {}
        assert conflicts == []

    def test_empty_file_nodes(self):
        crl = ConflictResolutionLayer()
        memory = {"fp:abc": "src/KpiRow.tsx"}
        cleaned, conflicts = crl.resolve(memory, {})
        assert cleaned == {}
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == ConflictType.STALE_MAPPING

    def test_all_valid_no_conflicts(self):
        crl = ConflictResolutionLayer()
        memory = {
            "fp:1": "src/A.tsx",
            "fp:2": "src/B.tsx",
        }
        files = {
            "src/A.tsx": _file("src/A.tsx", exports=["A"]),
            "src/B.tsx": _file("src/B.tsx", exports=["B"]),
        }
        cleaned, conflicts = crl.resolve(memory, files)
        assert cleaned == memory
        assert conflicts == []
