"""Binding v4 STEP 2 — Hard rule: compiler MUST NOT read node.data for UI props.

Invariants:
  1. compiler._build_node produces props={} when resolved_bindings is empty
  2. compiler._build_node produces props from resolved_bindings, NOT node.data
  3. When resolved_bindings has entry, node.data intent keys are NOT in props
"""

import pytest

from app.graphir.models import GraphIRNode, GraphIRDraft, EdgeRole, GraphIREdge
from app.graphir.layout import LayoutDerivationEngine
from app.graphir.compiler import UIIRCompiler
from app.binding.models import ResolvedBindings


class TestCompilerNeverReadsNodeDataForProps:
    """STEP 2 invariant: compiler is blind to node.data for UI props."""

    def _compile_root(self, node_type: str, node_data: dict, resolved: ResolvedBindings | None = None):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="r", type=node_type, data=node_data))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)
        tree = UIIRCompiler.compile(graph, layout, resolved_bindings=resolved)
        return tree.root

    def test_empty_resolved_bindings_produces_empty_props(self):
        """No resolved_bindings → props = {} regardless of node.data."""
        root = self._compile_root("KpiRow", {"metrics": ["revenue"]})
        assert root.props == {}

    def test_node_data_not_leaked_to_props(self):
        """node.data keys must NOT appear in props when resolved_bindings differs."""
        root = self._compile_root(
            "KpiRow",
            {"metrics": ["revenue"], "title": "Test"},
            resolved=ResolvedBindings(component_props={"KpiRow": {"data": "resolved"}}),
        )
        assert "metrics" not in root.props
        assert "title" not in root.props
        assert root.props.get("data") == "resolved"

    def test_resolved_bindings_is_source_of_truth(self):
        """Props come from resolved_bindings, not from node.data."""
        root = self._compile_root(
            "Timeseries",
            {"metric": "cpu", "chartTitle": "CPU Usage"},
            resolved=ResolvedBindings(component_props={"Timeseries": {"data": [1, 2, 3]}}),
        )
        assert root.props == {"data": [1, 2, 3]}

    def test_missing_component_in_resolved_bindings_is_empty(self):
        """Component not in resolved_bindings → props = {} (no fallback)."""
        root = self._compile_root(
            "AnalyticsTable",
            {"columns": ["A", "B"]},
            resolved=ResolvedBindings(component_props={"KpiRow": {"data": "x"}}),
        )
        assert root.props == {}

    def test_none_resolved_bindings_is_same_as_empty(self):
        """resolved_bindings=None treated as empty — props = {}."""
        root = self._compile_root("BarChart", {"metrics": [1, 2]}, resolved=None)
        assert root.props == {}
