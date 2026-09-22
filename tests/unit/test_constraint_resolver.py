"""IdentityResolver — unit tests for decision logic.

Pure Core: zero IO. Tests that resolve() correctly applies
the IDENTITY_SPEC rules of the current decision hierarchy.

F2 correction: these tests prove MEMORY INDEPENDENCE — the resolver's
output is a pure function of repository evidence and never of semantic
memory. They do NOT validate IdentityResolver as an architecturally
approved lifecycle authority (F1/F3 pending).
"""

import pytest

from app.graphir.constraint.resolver import IdentityResolver
from app.graphir.constraint.models import Decision, FileNode, MemoryRecord
from app.graphir.constraint.identity import CanonicalIdentity


def _identity(comp_name="Chart", cap="presentation.chart",
              domain=("generic",), params_hash="abc") -> CanonicalIdentity:
    return CanonicalIdentity(
        component_name=comp_name,
        capability_id=cap,
        domain=domain,
        params_hash=params_hash,
    )


def _file(path="src/Chart.tsx", exports=None, comp_names=None,
          domains=None, canonical_ids=None) -> FileNode:
    return FileNode(
        id=f"file:{path}",
        node_type="file",
        path=path,
        exports=exports or [],
        component_names=comp_names or [],
        domains=domains or ["generic"],
        canonical_ids=canonical_ids or [],
    )


class TestResolverEmptyWorkspace:
    """No files → all identities become CREATE."""

    def test_no_candidates_creates(self):
        resolver = IdentityResolver()
        identities = {"n0": _identity()}
        decisions = resolver.resolve(identities, {"n0": []}, {})
        assert decisions["n0"].decision == Decision.CREATE
        assert decisions["n0"].confidence == 0.0

    def test_multiple_identities_all_create(self):
        resolver = IdentityResolver()
        identities = {
            "n0": _identity(comp_name="A"),
            "n1": _identity(comp_name="B"),
        }
        decisions = resolver.resolve(identities, {"n0": [], "n1": []}, {})
        assert decisions["n0"].decision == Decision.CREATE
        assert decisions["n1"].decision == Decision.CREATE


class TestResolverThresholds:
    """Score-based decision rules."""

    def test_above_update_threshold(self):
        resolver = IdentityResolver()
        ident = _identity()
        candidates = {
            "n0": [(0.80, _file("src/Chart.tsx"))],
        }
        decisions = resolver.resolve({"n0": ident}, candidates, {})
        assert decisions["n0"].decision == Decision.UPDATE
        assert decisions["n0"].target_file == "src/Chart.tsx"
        assert abs(decisions["n0"].confidence - 0.80) < 0.001

    def test_between_thresholds_extends(self):
        resolver = IdentityResolver()
        ident = _identity()
        candidates = {
            "n0": [(0.45, _file("src/Chart.tsx"))],
        }
        decisions = resolver.resolve({"n0": ident}, candidates, {})
        assert decisions["n0"].decision == Decision.EXTEND
        assert decisions["n0"].target_file == "src/Chart.tsx"

    def test_below_extend_threshold_creates(self):
        resolver = IdentityResolver()
        ident = _identity()
        candidates = {
            "n0": [(0.20, _file("src/Chart.tsx"))],
        }
        decisions = resolver.resolve({"n0": ident}, candidates, {})
        assert decisions["n0"].decision == Decision.CREATE
        # DEFAULT path, not the candidate's path
        assert "components/Chart" in decisions["n0"].target_file

    def test_uses_best_candidate(self):
        resolver = IdentityResolver()
        ident = _identity()
        candidates = {
            "n0": [
                (0.30, _file("src/WeakMatch.tsx")),
                (0.85, _file("src/StrongMatch.tsx")),
            ],
        }
        decisions = resolver.resolve({"n0": ident}, candidates, {})
        assert decisions["n0"].decision == Decision.UPDATE
        assert decisions["n0"].target_file == "src/StrongMatch.tsx"


class TestResolverMemoryIndependence:
    """F2: memory content must never change the resolution result.

    These tests only demonstrate that IdentityResolver no longer has a
    memory channel. They do NOT endorse IdentityResolver as the final
    lifecycle authority — that remains the Confirmed Plan (F1/F3).
    """

    def test_resolver_rejects_memory_argument(self):
        fp = _identity().fingerprint()
        with pytest.raises(TypeError):
            IdentityResolver(resolved_mapping={
                fp: MemoryRecord(fingerprint=fp, file_path="src/Mapped.tsx", component_name="Chart"),
            })

    def test_resolver_has_no_memory_slot(self):
        resolver = IdentityResolver()
        assert not hasattr(resolver, "resolved_mapping")

    def test_memory_content_does_not_redirect_target(self):
        # Pre-F2, a persisted memory fact fp->src/Mapped.tsx forced the
        # decision onto src/Mapped.tsx (Level 1, confidence 1.0).
        # Simulate exactly that historical content:
        fp = _identity().fingerprint()
        pre_f2_memory = {
            fp: MemoryRecord(fingerprint=fp, file_path="src/Mapped.tsx", component_name="Chart"),
        }
        # Same input + same repository evidence in both runs:
        ident = _identity()
        candidates = {
            "n0": [(0.90, _file("src/Index.tsx"))],
        }
        file_nodes = {
            "src/Mapped.tsx": _file("src/Mapped.tsx"),
            "src/Index.tsx": _file("src/Index.tsx", comp_names=["Chart"]),
        }
        # F2 resolver (no memory channel): decision is a pure function of
        # {identity, candidates, file_nodes}. Memory content cannot change it.
        resolver = IdentityResolver()
        decisions = resolver.resolve({"n0": ident}, candidates, file_nodes)
        assert decisions["n0"].target_file == "src/Index.tsx"
        assert decisions["n0"].decision == Decision.UPDATE
        # The historical memory fact is dead: nothing toggles the target.
        assert decisions["n0"].target_file != pre_f2_memory[fp].file_path


class TestResolverLevel2:
    """Known identity in file index takes priority over scoring."""

    def test_canonical_id_in_file_index_updates(self):
        ident = _identity()
        fp = ident.fingerprint()
        resolver = IdentityResolver()
        candidates = {
            "n0": [(0.20, _file("src/Other.tsx"))],
        }
        file_nodes = {
            "src/Existing.tsx": _file(
                "src/Existing.tsx",
                canonical_ids=[fp],
            ),
            "src/Other.tsx": _file("src/Other.tsx"),
        }
        decisions = resolver.resolve({"n0": ident}, candidates, file_nodes)
        assert decisions["n0"].target_file == "src/Existing.tsx"
        assert abs(decisions["n0"].confidence - 0.95) < 0.001

    def test_canonical_id_checked_across_all_files(self):
        ident = _identity(cap="presentation.kpi_row")
        fp = ident.fingerprint()
        resolver = IdentityResolver()
        candidates = {
            "n0": [(0.90, _file("src/Unrelated.tsx"))],
        }
        file_nodes = {
            "src/Unrelated.tsx": _file("src/Unrelated.tsx"),
            "src/KpiRow.tsx": _file(
                "src/KpiRow.tsx",
                exports=["KpiRow"],
                canonical_ids=[fp],
            ),
        }
        decisions = resolver.resolve({"n0": ident}, candidates, file_nodes)
        assert decisions["n0"].target_file == "src/KpiRow.tsx"


class TestResolverDeterminism:
    """Same inputs → same decisions every time."""

    def test_deterministic_across_calls(self):
        ident = _identity()
        candidates = {"n0": [(0.80, _file("src/Chart.tsx"))]}
        file_nodes = {"src/Chart.tsx": _file("src/Chart.tsx")}

        resolver = IdentityResolver()
        d1 = resolver.resolve({"n0": ident}, candidates, file_nodes)
        d2 = resolver.resolve({"n0": ident}, candidates, file_nodes)

        for key in d1:
            assert d1[key].decision == d2[key].decision
            assert d1[key].target_file == d2[key].target_file
            assert abs(d1[key].confidence - d2[key].confidence) < 0.001

    def test_best_candidate_wins_regardless_of_insertion_order(self):
        """FileNode dict insertion order does not affect best candidate."""
        ident = _identity()
        candidates = {
            "n0": [
                (0.30, _file("src/B.tsx")),
                (0.90, _file("src/A.tsx")),
            ],
        }
        resolver = IdentityResolver()
        decisions = resolver.resolve({"n0": ident}, candidates, {})
        assert decisions["n0"].target_file == "src/A.tsx"


class TestResolverEdgeCases:
    """Edge cases and error handling."""

    def test_no_identities_returns_empty(self):
        resolver = IdentityResolver()
        decisions = resolver.resolve({}, {}, {})
        assert decisions == {}

    def test_confidence_matches_best_score(self):
        ident = _identity()
        candidates = {"n0": [(0.75, _file("src/Chart.tsx"))]}
        resolver = IdentityResolver()
        decisions = resolver.resolve({"n0": ident}, candidates, {})
        assert abs(decisions["n0"].confidence - 0.75) < 0.001
