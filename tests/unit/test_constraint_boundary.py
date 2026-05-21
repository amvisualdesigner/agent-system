"""GraphIR Purity Boundary — tests for enforce_graphir_purity.

These tests verify that filesystem concepts cannot leak into GraphIR.
No IO, no env vars, 100% deterministic.
"""

import pytest

from app.graphir.boundary import (
    enforce_graphir_purity,
    enforce_graph_purity,
    GraphIRBoundaryViolation,
    GRAPHIR_BLOCKED_KEYS,
    GRAPHIR_BLOCKED_PREFIXES,
)


class TestEnforceGraphIRPurity:
    """Unit tests for the purity enforcement function."""

    def test_allows_semantic_data(self):
        """Semantic metadata (domain, priority) must be allowed."""
        data = {"domain": "analytics", "priority": "high", "grouping": "sales"}
        enforce_graphir_purity(data, "test")  # should not raise

    def test_allows_empty_dict(self):
        enforce_graphir_purity({}, "test")

    def test_allows_arbitrary_semantic_keys(self):
        """Business-specific keys like 'metric', 'dimension' must be allowed."""
        data = {"metric": "revenue", "dimension": "region", "top_k": 5}
        enforce_graphir_purity(data, "test")

    def test_blocks_file_path_key(self):
        """file_path is explicitly a filesystem concept."""
        with pytest.raises(GraphIRBoundaryViolation, match="file_path"):
            enforce_graphir_purity({"file_path": "src/foo.tsx"}, "test")

    def test_blocks_fs_path_key(self):
        with pytest.raises(GraphIRBoundaryViolation, match="fs_path"):
            enforce_graphir_purity({"fs_path": "/tmp/foo"}, "test")

    def test_blocks_workspace_key(self):
        with pytest.raises(GraphIRBoundaryViolation, match="workspace"):
            enforce_graphir_purity({"workspace": "/repo"}, "test")

    def test_blocks_worktree_key(self):
        with pytest.raises(GraphIRBoundaryViolation, match="worktree"):
            enforce_graphir_purity({"worktree": "agent-abc123"}, "test")

    def test_blocks_absolute_path_key(self):
        with pytest.raises(GraphIRBoundaryViolation):
            enforce_graphir_purity({"absolute_path": "/tmp/x"}, "test")

    def test_blocks_relative_path_key(self):
        with pytest.raises(GraphIRBoundaryViolation):
            enforce_graphir_purity({"relative_path": "./x"}, "test")

    def test_blocks_fs_prefixed_key(self):
        """Any key starting with 'fs_' is blocked."""
        with pytest.raises(GraphIRBoundaryViolation, match="fs_"):
            enforce_graphir_purity({"fs_something": "value"}, "test")

    def test_blocks_path_prefixed_key(self):
        """Any key starting with 'path_' is blocked."""
        with pytest.raises(GraphIRBoundaryViolation, match="path_"):
            enforce_graphir_purity({"path_something": "value"}, "test")

    def test_blocks_file_prefixed_key(self):
        """Any key starting with 'file_' is blocked."""
        with pytest.raises(GraphIRBoundaryViolation, match="file_"):
            enforce_graphir_purity({"file_something": "value"}, "test")

    def test_blocks_multiple_violations_reports_first(self):
        """First blocked key found should be reported."""
        with pytest.raises(GraphIRBoundaryViolation) as exc:
            enforce_graphir_purity({"file_path": "a.tsx", "workspace": "/repo"}, "test")
        assert "file_path" in str(exc.value)

    def test_context_is_included_in_error(self):
        """The context string should appear in the error message."""
        with pytest.raises(GraphIRBoundaryViolation) as exc:
            enforce_graphir_purity({"file_path": "x"}, "my_context")
        assert "my_context" in str(exc.value)


class TestEnforceGraphPurity:
    """Tests for the convenience wrapper that scans all graph nodes."""

    def test_accepts_clean_graph(self):
        """A GraphIR with only semantic data should pass."""
        from app.graphir.models import GraphIR, GraphIRNode, GraphIRLayout

        graph = GraphIR(
            nodes={
                "n1": GraphIRNode(id="n1", type="Page", data={}, metadata={"domain": "sales"}),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        enforce_graph_purity(graph)  # should not raise

    def test_rejects_graph_with_file_path_in_data(self):
        from app.graphir.models import GraphIR, GraphIRNode, GraphIRLayout

        graph = GraphIR(
            nodes={
                "n1": GraphIRNode(id="n1", type="Page", data={"file_path": "src/Page.tsx"}),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        with pytest.raises(GraphIRBoundaryViolation, match="file_path"):
            enforce_graph_purity(graph)

    def test_rejects_graph_with_file_path_in_metadata(self):
        from app.graphir.models import GraphIR, GraphIRNode, GraphIRLayout

        graph = GraphIR(
            nodes={
                "n1": GraphIRNode(id="n1", type="Page", metadata={"file_path": "src/Page.tsx"}),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        with pytest.raises(GraphIRBoundaryViolation, match="file_path"):
            enforce_graph_purity(graph)

    def test_reports_correct_node_id_in_error(self):
        from app.graphir.models import GraphIR, GraphIRNode, GraphIRLayout

        graph = GraphIR(
            nodes={
                "safe": GraphIRNode(id="safe", type="Safe", data={"domain": "ok"}),
                "bad": GraphIRNode(id="bad", type="Bad", data={"fs_path": "/tmp/x"}),
            },
            edges=[],
            layout=GraphIRLayout(root="safe"),
        )
        with pytest.raises(GraphIRBoundaryViolation) as exc:
            enforce_graph_purity(graph)
        assert "bad" in str(exc.value)


class TestGraphIRBlockedKeysConstant:
    """Tests for the blocked keys configuration."""

    def test_file_path_is_blocked(self):
        assert "file_path" in GRAPHIR_BLOCKED_KEYS

    def test_workspace_is_blocked(self):
        assert "workspace" in GRAPHIR_BLOCKED_KEYS

    def test_worktree_is_blocked(self):
        assert "worktree" in GRAPHIR_BLOCKED_KEYS

    def test_fs_prefix_exists(self):
        assert "fs_" in GRAPHIR_BLOCKED_PREFIXES

    def test_path_prefix_exists(self):
        assert "path_" in GRAPHIR_BLOCKED_PREFIXES
