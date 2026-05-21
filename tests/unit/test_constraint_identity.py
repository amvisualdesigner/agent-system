"""CanonicalIdentity — unit tests for deterministic fingerprinting.

Pure Core: zero IO, 100% deterministic.
"""

from app.graphir.constraint.identity import CanonicalIdentity, build_identities
from app.graphir.models import GraphIR, GraphIRNode, GraphIRLayout


class TestCanonicalIdentity:
    """Tests for the CanonicalIdentity dataclass and fingerprint."""

    def test_fingerprint_includes_all_parts(self):
        identity = CanonicalIdentity(
            component_name="KpiRow",
            capability_id="presentation.kpi_row",
            domain=("analytics",),
            params_hash="abc123def456",
        )
        fp = identity.fingerprint()
        assert "presentation.kpi_row" in fp
        assert "analytics" in fp
        assert "abc123def456" in fp

    def test_same_inputs_same_fingerprint(self):
        a = CanonicalIdentity("KpiRow", "presentation.kpi_row", ("analytics",), "abc")
        b = CanonicalIdentity("KpiRow", "presentation.kpi_row", ("analytics",), "abc")
        assert a.fingerprint() == b.fingerprint()

    def test_different_params_different_fingerprint(self):
        a = CanonicalIdentity("KpiRow", "presentation.kpi_row", ("analytics",), "abc")
        b = CanonicalIdentity("KpiRow", "presentation.kpi_row", ("analytics",), "def")
        assert a.fingerprint() != b.fingerprint()

    def test_different_domain_different_fingerprint(self):
        a = CanonicalIdentity("KpiRow", "presentation.kpi_row", ("analytics",), "abc")
        b = CanonicalIdentity("KpiRow", "presentation.kpi_row", ("sales",), "abc")
        assert a.fingerprint() != b.fingerprint()

    def test_different_capability_different_fingerprint(self):
        a = CanonicalIdentity("KpiRow", "presentation.kpi_row", ("analytics",), "abc")
        b = CanonicalIdentity("Table", "presentation.table", ("analytics",), "abc")
        assert a.fingerprint() != b.fingerprint()

    def test_domain_order_normalized(self):
        a = CanonicalIdentity("X", "cap", ("b", "a"), "h")
        b = CanonicalIdentity("X", "cap", ("a", "b"), "h")
        assert a.fingerprint() == b.fingerprint()

    def test_generic_domain_when_empty(self):
        identity = CanonicalIdentity("Foo", "cap", (), "h")
        assert "generic" in identity.fingerprint()


class TestBuildIdentities:
    """Tests for building identities from a GraphIR instance."""

    def test_single_node(self):
        graph = GraphIR(
            nodes={
                "n1": GraphIRNode(id="n1", type="KpiRow", data={}, metadata={}),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        identities = build_identities(graph)
        assert len(identities) == 1
        assert "n1" in identities
        assert identities["n1"].component_name == "KpiRow"
        assert identities["n1"].capability_id == "KpiRow"  # falls back to type

    def test_uses_capability_from_metadata(self):
        graph = GraphIR(
            nodes={
                "n1": GraphIRNode(
                    id="n1", type="KpiRow", data={},
                    metadata={"capability": "presentation.kpi_row"},
                ),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        identities = build_identities(graph)
        assert identities["n1"].capability_id == "presentation.kpi_row"

    def test_uses_domain_from_metadata(self):
        graph = GraphIR(
            nodes={
                "n1": GraphIRNode(
                    id="n1", type="KpiRow", data={},
                    metadata={"domain": "analytics"},
                ),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        identities = build_identities(graph)
        assert "analytics" in identities["n1"].domain

    def test_multiple_domains(self):
        graph = GraphIR(
            nodes={
                "n1": GraphIRNode(
                    id="n1", type="KpiRow", data={},
                    metadata={"domain": ["analytics", "sales"]},
                ),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        identities = build_identities(graph)
        assert "analytics" in identities["n1"].domain
        assert "sales" in identities["n1"].domain

    def test_params_affect_hash(self):
        graph_a = GraphIR(
            nodes={
                "n1": GraphIRNode(id="n1", type="KpiRow", data={"metric": "revenue"}),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        graph_b = GraphIR(
            nodes={
                "n1": GraphIRNode(id="n1", type="KpiRow", data={"metric": "cost"}),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        id_a = build_identities(graph_a)
        id_b = build_identities(graph_b)
        assert id_a["n1"].fingerprint() != id_b["n1"].fingerprint()

    def test_deterministic_across_calls(self):
        graph = GraphIR(
            nodes={
                "n1": GraphIRNode(
                    id="n1", type="KpiRow", data={"metric": "revenue"},
                    metadata={"capability": "presentation.kpi_row", "domain": "analytics"},
                ),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        fp1 = build_identities(graph)["n1"].fingerprint()
        fp2 = build_identities(graph)["n1"].fingerprint()
        assert fp1 == fp2
