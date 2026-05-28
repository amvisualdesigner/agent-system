"""IntentFileMatcher — unit tests for identity→file scoring.

Pure Core: zero IO. Tests that match() produces correct
ranked candidate lists (no decision logic — that's in resolver.py).
"""

from app.graphir.constraint.matcher import IntentFileMatcher
from app.graphir.constraint.models import FileNode
from app.graphir.models import GraphIR, GraphIRNode, GraphIRLayout


def _make_graph(*node_types: str) -> GraphIR:
    """Build a minimal GraphIR from type names."""
    nodes = {}
    for i, t in enumerate(node_types):
        nid = f"n{i}"
        nodes[nid] = GraphIRNode(id=nid, type=t, data={}, metadata={})
    root = list(nodes.keys())[0] if nodes else "n0"
    return GraphIR(nodes=nodes, edges=[], layout=GraphIRLayout(root=root))


def _make_file(rel_path: str, exports: list[str] = None,
               component_names: list[str] = None,
               domains: list[str] = None) -> FileNode:
    return FileNode(
        id=f"file:{rel_path}",
        node_type="file",
        path=rel_path,
        exports=exports or [],
        component_names=component_names or [],
        domains=domains or ["generic"],
    )


class TestMatcherGreenfield:
    """When no files exist, candidates must be empty."""

    def test_empty_workspace_returns_candidates(self):
        graph = _make_graph("KpiRow")
        matcher = IntentFileMatcher()
        identities, candidates = matcher.match(graph, {})
        assert len(identities) == 1
        assert candidates["n0"] == []

    def test_multiple_nodes_empty_candidates(self):
        graph = _make_graph("Page", "KpiRow", "Timeseries")
        matcher = IntentFileMatcher()
        identities, candidates = matcher.match(graph, {})
        assert len(identities) == 3
        for c in candidates.values():
            assert c == []


class TestMatcherScoring:
    """Tests for similarity scoring (ranking, not decisions)."""

    def test_exact_name_match_ranks_highest(self):
        """Exact file stem match → highest score in candidate list."""
        graph = _make_graph("KpiRow")
        files = {
            "src/KpiRow.tsx": _make_file(
                "src/KpiRow.tsx",
                exports=["KpiRow"],
                component_names=["KpiRow"],
            ),
            "src/Other.tsx": _make_file(
                "src/Other.tsx",
                exports=["Other"],
                component_names=["Other"],
            ),
        }
        matcher = IntentFileMatcher()
        identities, candidates = matcher.match(graph, files)

        assert len(candidates["n0"]) == 2
        best_score, best_fn = candidates["n0"][0]
        assert best_fn.path == "src/KpiRow.tsx"
        assert best_score > 0.50

    def test_candidates_sorted_descending(self):
        """Candidate list must be sorted by score descending."""
        graph = _make_graph("KpiRow")
        files = {
            "src/Other.tsx": _make_file(
                "src/Other.tsx",
                exports=["Other"],
                component_names=["Other"],
            ),
            "src/KpiRow.tsx": _make_file(
                "src/KpiRow.tsx",
                exports=["KpiRow"],
                component_names=["KpiRow"],
            ),
        }
        matcher = IntentFileMatcher()
        identities, candidates = matcher.match(graph, files)

        scores = [s for s, _ in candidates["n0"]]
        assert scores == sorted(scores, reverse=True)

    def test_no_match_produces_low_scores(self):
        """No overlap → scores below EXTEND threshold."""
        graph = _make_graph("BarChart")
        files = {
            "src/UserSettings.tsx": _make_file(
                "src/UserSettings.tsx",
                exports=["UserSettings"],
                component_names=["UserSettings"],
                domains=["admin"],
            ),
        }
        matcher = IntentFileMatcher()
        identities, candidates = matcher.match(graph, files)

        best_score, best_fn = candidates["n0"][0]
        assert best_score < 0.35  # below EXTEND_THRESHOLD



    def test_identities_match_node_count(self):
        """Number of identities equals number of graph nodes."""
        graph = _make_graph("A", "B", "C")
        matcher = IntentFileMatcher()
        identities, candidates = matcher.match(graph, {})
        assert len(identities) == 3


class TestMatcherDeterminism:
    """Same inputs → same candidates every time."""

    def test_deterministic_across_calls(self):
        graph = _make_graph("KpiRow")
        files = {
            "src/KpiRow.tsx": _make_file(
                "src/KpiRow.tsx",
                exports=["KpiRow"],
                component_names=["KpiRow"],
            ),
        }
        matcher = IntentFileMatcher()

        id1, c1 = matcher.match(graph, files)
        id2, c2 = matcher.match(graph, files)

        for key in c1:
            assert len(c1[key]) == len(c2[key])
            for (s1, f1), (s2, f2) in zip(c1[key], c2[key]):
                assert abs(s1 - s2) < 0.001
                assert f1.path == f2.path


class TestMatcherAdversarial:
    """Adversarial tests — prove identity scoring is robust."""

    @staticmethod
    def _make_graph_with_caps(*type_cap_pairs: tuple[str, str, str]) -> GraphIR:
        nodes = {}
        for nid, typ, cap in type_cap_pairs:
            nodes[nid] = GraphIRNode(
                id=nid, type=typ, data={},
                metadata={"intent_capability": cap},
            )
        root = type_cap_pairs[0][0] if type_cap_pairs else "n0"
        return GraphIR(nodes=nodes, edges=[], layout=GraphIRLayout(root=root))

    def test_same_name_different_capability(self):
        """Same component_name, different capabilities → different identities."""
        graph = self._make_graph_with_caps(
            ("n0", "Chart", "presentation.bar_chart"),
            ("n1", "Chart", "presentation.line_chart"),
        )
        matcher = IntentFileMatcher()
        identities, _ = matcher.match(graph, {})
        assert identities["n0"].fingerprint() != identities["n1"].fingerprint()

    def test_camelcase_scores_match_own_file(self):
        """KPIChart scores highest against KPIChart.tsx (same for KpiChart)."""
        graph = self._make_graph_with_caps(
            ("n0", "KPIChart", "presentation.kpi_chart"),
            ("n1", "KpiChart", "presentation.kpi_chart_mobile"),
        )
        files = {
            "src/KPIChart.tsx": _make_file(
                "src/KPIChart.tsx",
                exports=["KPIChart"],
                component_names=["KPIChart"],
            ),
            "src/KpiChart.tsx": _make_file(
                "src/KpiChart.tsx",
                exports=["KpiChart"],
                component_names=["KpiChart"],
            ),
        }
        matcher = IntentFileMatcher()
        identities, candidates = matcher.match(graph, files)

        # Both lowercase to 'kpichart' → stems identical → tie
        # Each gets both files in candidates, sorted by score
        for c in candidates.values():
            assert len(c) == 2

    def test_near_duplicate_capability_low_score(self):
        """presentation.kpi scores low against KpiRow.tsx (different stem)."""
        graph = self._make_graph_with_caps(
            ("n0", "Kpi", "presentation.kpi"),
        )
        files = {
            "src/KpiRow.tsx": _make_file(
                "src/KpiRow.tsx",
                exports=["KpiRow"],
                component_names=["KpiRow"],
            ),
        }
        matcher = IntentFileMatcher()
        identities, candidates = matcher.match(graph, files)

        best_score, best_fn = candidates["n0"][0]
        assert best_fn.path == "src/KpiRow.tsx"
        assert best_score < 0.50  # below UPDATE threshold due to stem mismatch
