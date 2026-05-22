"""IdentityResolver — unit tests for decision logic.

Pure Core: zero IO. Tests that resolve() correctly applies
the IDENTITY_SPEC rules (levels 1-3 of decision hierarchy).
"""

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


class TestResolverLevel1:
    """Resolved mapping takes priority over scoring."""

    def test_resolved_mapping_overrides_scoring(self):
        fp = _identity().fingerprint()
        resolver = IdentityResolver(resolved_mapping={
            fp: MemoryRecord(fingerprint=fp, file_path="src/Mapped.tsx", component_name="Chart"),
        })
        ident = _identity()
        candidates = {
            "n0": [(0.90, _file("src/Unmapped.tsx"))],
        }
        file_nodes = {
            "src/Mapped.tsx": _file("src/Mapped.tsx"),
            "src/Unmapped.tsx": _file("src/Unmapped.tsx"),
        }
        decisions = resolver.resolve({"n0": ident}, candidates, file_nodes)
        assert decisions["n0"].target_file == "src/Mapped.tsx"
        assert decisions["n0"].confidence == 1.0
        assert decisions["n0"].decision == Decision.UPDATE

    def test_resolved_mapping_ignored_if_file_missing(self):
        fp = _identity().fingerprint()
        resolver = IdentityResolver(resolved_mapping={
            fp: MemoryRecord(fingerprint=fp, file_path="src/Gone.tsx", component_name="Chart"),
        })
        ident = _identity()
        candidates = {
            "n0": [(0.90, _file("src/Existing.tsx"))],
        }
        file_nodes = {
            "src/Existing.tsx": _file("src/Existing.tsx"),
        }
        decisions = resolver.resolve({"n0": ident}, candidates, file_nodes)
        # Falls through to scoring — mapped file doesn't exist
        assert decisions["n0"].target_file == "src/Existing.tsx"

    def test_resolved_mapping_empty_dict_no_effect(self):
        resolver = IdentityResolver(resolved_mapping={})
        ident = _identity()
        candidates = {
            "n0": [(0.80, _file("src/Chart.tsx"))],
        }
        decisions = resolver.resolve({"n0": ident}, candidates, {})
        assert decisions["n0"].decision == Decision.UPDATE
        assert decisions["n0"].target_file == "src/Chart.tsx"


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
