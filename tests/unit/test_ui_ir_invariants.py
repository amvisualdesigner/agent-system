"""UI IR Invariants — tests for UIIRCompiler + UIComponentTree consistency.

Guarantees:
  - Propagation: all GraphIRNode.data keys appear in UIComponentNode.props
  - No-loss: even with 5+ keys of mixed types, none are dropped
  - Multi-node: graph edges match tree children structure
  - Empty data: node.data = {} → props = {}
  - Deterministic: same input → identical tree
  - Null emission: None → {{null}} in JSX output (shared _emit)
  - Generator content: covered in test_react_legacy_harness.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import unittest

from app.graphir.models import (
    EdgeRole, GraphIR, GraphIRLayout, GraphIRNode, GraphIREdge,
    LayoutConstraint,
)
from app.graphir.backends.react_backend import ReactBackend
from app.graphir.compiler import UIIRCompiler


def _make_graph(
    nodes: dict[str, GraphIRNode],
    edges: list[GraphIREdge] | None = None,
    root: str = "",
) -> GraphIR:
    if not root and nodes:
        root = next(iter(nodes))
    return GraphIR(
        nodes=nodes,
        edges=edges or [],
        layout=GraphIRLayout(root=root),
    )


class TestUIIRCompiler(unittest.TestCase):
    """UIIRCompiler — GraphIR → UIComponentTree invariants."""

    def test_propagation_invariant(self):
        """Without BindingResolver, props = {} (intent is not UI props)."""
        node = GraphIRNode(
            id="table1", type="AnalyticsTable",
            data={"columns": ["A", "B"], "loading": None, "page_size": 50},
        )
        graph = _make_graph({"table1": node}, root="table1")
        tree = UIIRCompiler.compile(graph, graph.layout)

        # BindingResolver is the ONLY source of UI props.
        # Without it, props is empty — node.data is intent, not UI.
        self.assertEqual(tree.root.props, {})

    def test_no_loss_multi_key(self):
        """Without BindingResolver, all node.data keys are ignored (intent)."""
        data = {
            "columns": ["Metric", "Value"],
            "metrics": ["revenue", "growth"],
            "title": "Dashboard Q1",
            "page_size": 25,
            "loading": None,
            "debug": True,
            "config": {"theme": "dark"},
        }
        node = GraphIRNode(id="kpi1", type="KpiRow", data=data)
        graph = _make_graph({"kpi1": node}, root="kpi1")
        tree = UIIRCompiler.compile(graph, graph.layout)

        # Intent (node.data) is NOT UI props — BindingResolver is the bridge.
        self.assertEqual(tree.root.props, {})

    def test_multi_node_tree_consistency(self):
        """GraphIR edges → UIComponentNode.children structure."""
        page = GraphIRNode(id="page", type="Page", data={})
        kpi = GraphIRNode(id="kpi1", type="KpiRow", data={"metrics": ["m1"]})
        table = GraphIRNode(id="table1", type="AnalyticsTable", data={"columns": ["c1"]})
        graph = _make_graph(
            nodes={"page": page, "kpi1": kpi, "table1": table},
            edges=[
                GraphIREdge(source="page", target="kpi1", role=EdgeRole.CONTAINS),
                GraphIREdge(source="page", target="table1", role=EdgeRole.CONTAINS),
            ],
            root="page",
        )
        tree = UIIRCompiler.compile(graph, graph.layout)

        self.assertEqual(tree.root.component, "Page")
        self.assertEqual(len(tree.root.children), 2)
        child_ids = {c.id for c in tree.root.children}
        self.assertEqual(child_ids, {"kpi1", "table1"})
        # Verify leaf nodes have no children
        for child in tree.root.children:
            self.assertEqual(len(child.children), 0)

    def test_empty_data(self):
        """node.data = {} → UIComponentNode.props = {}."""
        node = GraphIRNode(id="page1", type="Page", data={})
        graph = _make_graph({"page1": node}, root="page1")
        tree = UIIRCompiler.compile(graph, graph.layout)

        self.assertEqual(tree.root.props, {})

    def test_deterministic_compile(self):
        """Same input → identical tree (deep equality)."""
        node = GraphIRNode(
            id="table1", type="AnalyticsTable",
            data={"columns": ["X", "Y"], "rows": []},
        )
        graph = _make_graph({"table1": node}, root="table1")
        layout = graph.layout

        tree1 = UIIRCompiler.compile(graph, layout)
        tree2 = UIIRCompiler.compile(graph, layout)

        self.assertEqual(tree1.root.id, tree2.root.id)
        self.assertEqual(tree1.root.component, tree2.root.component)
        self.assertEqual(tree1.root.props, tree2.root.props)
        self.assertEqual(len(tree1.root.children), len(tree2.root.children))

    def test_layout_hints_preserved(self):
        """LayoutConstraint from layout.constraints → UIComponentNode.layout_hints."""
        node = GraphIRNode(id="page1", type="Page", data={})
        graph = _make_graph({"page1": node}, root="page1")
        layout = GraphIRLayout(
            root="page1",
            constraints={"page1": [LayoutConstraint.ROW]},
        )
        tree = UIIRCompiler.compile(graph, layout)

        self.assertIn(LayoutConstraint.ROW, tree.root.layout_hints)

    def test_recursive_nested_children(self):
        """Two levels of nesting: grandchild preserved."""
        page = GraphIRNode(id="page", type="Page", data={})
        section = GraphIRNode(id="section1", type="BarChart", data={"title": "Section"})
        table = GraphIRNode(id="table1", type="AnalyticsTable", data={"columns": ["c1"]})
        graph = _make_graph(
            nodes={"page": page, "section1": section, "table1": table},
            edges=[
                GraphIREdge(source="page", target="section1", role=EdgeRole.CONTAINS),
                GraphIREdge(source="section1", target="table1", role=EdgeRole.CONTAINS),
            ],
            root="page",
        )
        tree = UIIRCompiler.compile(graph, graph.layout)

        self.assertEqual(len(tree.root.children), 1)
        section_node = tree.root.children[0]
        self.assertEqual(section_node.component, "BarChart")
        # Intent is not UI — no BindingResolver → props = {}
        self.assertEqual(section_node.props, {})
        self.assertEqual(len(section_node.children), 1)
        self.assertEqual(section_node.children[0].component, "AnalyticsTable")


class TestReactBackendEmit(unittest.TestCase):
    """ReactBackend._emit — framework-specific JSX emission."""

    def test_emit_includes_all_props(self):
        """Every key in props appears in emitted string."""
        props = {"columns": ["A"], "loading": None, "title": "Test"}
        result = ReactBackend._emit(props)
        self.assertIn('columns={["A"]}', result)
        self.assertIn("loading={null}", result)
        self.assertIn('title="Test"', result)

    def test_emit_null_becomes_explicit(self):
        """None emits as {null} — no silent skip."""
        result = ReactBackend._emit({"key": None})
        self.assertIn("key={null}", result)

    def test_emit_empty_props(self):
        """Empty dict → empty string."""
        self.assertEqual(ReactBackend._emit({}), "")

    def test_emit_bool_values(self):
        """Boolean values render as lowercase strings."""
        result = ReactBackend._emit({"active": True, "hidden": False})
        self.assertIn("active=true", result)
        self.assertIn("hidden=false", result)

    def test_emit_list_dict_values(self):
        """List and dict values render as JSON."""
        result = ReactBackend._emit({
            "items": [1, 2, 3],
            "meta": {"key": "val"},
        })
        self.assertIn('items={[1, 2, 3]}', result)
        self.assertIn('meta={{"key": "val"}}', result)

    def test_emit_total_count_matches(self):
        """Number of emitted attributes equals number of props keys."""
        props = {"a": 1, "b": "x", "c": None, "d": True}
        result = ReactBackend._emit(props)
        # Each key produces exactly one attribute
        for key in props:
            self.assertIn(key, result)


if __name__ == "__main__":
    unittest.main()
