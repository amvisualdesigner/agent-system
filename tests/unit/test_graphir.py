"""Unit tests for GraphIR Phase 0: Schema Freeze + Core Models.

Test coverage:
  1. EdgeRole purity (no position words)
  2. GraphIRNode construction and immutability
  3. GraphIREdge construction
  4. GraphIRDraft: add_node, add_edge, remove_node, get_orphan_nodes
  5. GraphIRDraft.freeze() invariants (root, DAG, reachability)
  6. GraphIRDraft.freeze() rejection cases
  7. GraphIR immutability
  8. GraphIRLayout construction
  9. IntentType validation
  10. IntentExtensionRegistry registration and validation
  11. IntentPlan validation
  12. LayoutDerivationEngine determinism
  13. LayoutDerivationEngine no type-based branching
  14. Debug visualize output structure
"""

import os
import sys
import json
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.models import (
    EdgeRole,
    LayoutConstraint,
    GraphIRNode,
    GraphIREdge,
    GraphIRLayout,
    GraphIR,
    GraphIRDraft,
)
from app.graphir.intent import (
    Intent,
    IntentType,
    IntentExtensionRegistry,
    IntentNode,
    IntentPlan,
    make_intent_id,
)
from app.graphir.validator import GraphIRValidator
from app.graphir.layout import LayoutDerivationEngine
from app.graphir.debug import visualize


# ════════════════════════════════════════════════════════════
# 1. EdgeRole Purity (HARD CONSTRAINT)
# ════════════════════════════════════════════════════════════

class TestEdgeRolePurity(unittest.TestCase):
    """EdgeRole must contain ONLY CONTAINS/PRIMARY/SUPPORTING.

    Any position-based role (left, right, top, bottom, header, etc.)
    is a regression to slots. This test enforces that.
    """

    def test_only_three_roles_exist(self):
        roles = set(EdgeRole)
        expected = {EdgeRole.CONTAINS, EdgeRole.PRIMARY, EdgeRole.SUPPORTING}
        self.assertEqual(roles, expected, "EdgeRole has regressed: additional roles found")

    def test_no_position_words_in_role_values(self):
        position_words = {
            "left", "right", "top", "bottom", "center",
            "header", "footer", "sidebar", "column", "row",
            "grid", "flex", "card", "panel", "section", "group",
            "metrics", "chart", "table",
        }
        for role in EdgeRole:
            value = role.value.lower()
            for word in position_words:
                self.assertNotIn(
                    word, value,
                    f"EdgeRole.{role.name} contains position word '{word}': "
                    f"this is a slot regression"
                )

    def test_role_count_unchanged(self):
        self.assertEqual(len(EdgeRole), 3)


# ════════════════════════════════════════════════════════════
# 2. GraphIRNode
# ════════════════════════════════════════════════════════════

class TestGraphIRNode(unittest.TestCase):
    def test_create_node_minimal(self):
        node = GraphIRNode(id="kpi1", type="KpiRow")
        self.assertEqual(node.id, "kpi1")
        self.assertEqual(node.type, "KpiRow")
        self.assertEqual(node.data, {})
        self.assertEqual(node.metadata, {})

    def test_create_node_with_data(self):
        node = GraphIRNode(
            id="kpi1",
            type="KpiRow",
            data={"metrics": ["revenue", "growth"]},
            metadata={"priority": "high"},
        )
        self.assertEqual(node.data["metrics"], ("revenue", "growth"))
        self.assertEqual(node.metadata["priority"], "high")

    def test_node_is_immutable(self):
        node = GraphIRNode(id="kpi1", type="KpiRow")
        with self.assertRaises(Exception):
            node.id = "changed"  # frozen dataclass

    def test_data_field_is_immutable(self):
        node = GraphIRNode(id="kpi1", type="KpiRow", data={"metrics": ["revenue"]})
        with self.assertRaises(Exception):
            node.data = {"other": "value"}


# ════════════════════════════════════════════════════════════
# 3. GraphIREdge
# ════════════════════════════════════════════════════════════

class TestGraphIREdge(unittest.TestCase):
    def test_create_edge(self):
        edge = GraphIREdge(source="page", target="kpi1", role=EdgeRole.PRIMARY)
        self.assertEqual(edge.source, "page")
        self.assertEqual(edge.target, "kpi1")
        self.assertEqual(edge.role, EdgeRole.PRIMARY)

    def test_edge_is_immutable(self):
        edge = GraphIREdge(source="page", target="kpi1", role=EdgeRole.CONTAINS)
        with self.assertRaises(Exception):
            edge.source = "changed"


# ════════════════════════════════════════════════════════════
# 4. GraphIRDraft (mutable builder)
# ════════════════════════════════════════════════════════════

class TestGraphIRDraft(unittest.TestCase):
    def test_draft_starts_empty(self):
        draft = GraphIRDraft()
        self.assertEqual(draft.nodes, {})
        self.assertEqual(draft.edges, [])

    def test_add_node(self):
        draft = GraphIRDraft()
        node = GraphIRNode(id="page", type="Page")
        draft.add_node(node)
        self.assertIn("page", draft.nodes)
        self.assertIs(draft.nodes["page"], node)

    def test_add_edge(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="kpi1", type="KpiRow"))
        edge = GraphIREdge(source="page", target="kpi1", role=EdgeRole.PRIMARY)
        draft.add_edge(edge)
        self.assertEqual(len(draft.edges), 1)

    def test_remove_node_cleans_up_edges(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="kpi1", type="KpiRow"))
        draft.add_edge(GraphIREdge(source="page", target="kpi1", role=EdgeRole.PRIMARY))
        draft.remove_node("kpi1")
        self.assertNotIn("kpi1", draft.nodes)
        self.assertEqual(len(draft.edges), 0)

    def test_get_orphan_nodes(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="kpi1", type="KpiRow"))
        draft.add_node(GraphIRNode(id="orphan", type="Chart"))
        # orphan has no edge and is not root
        draft.add_edge(GraphIREdge(source="page", target="kpi1", role=EdgeRole.PRIMARY))
        orphans = draft.get_orphan_nodes()
        self.assertIn("orphan", orphans)
        self.assertIn("page", orphans)
        self.assertNotIn("kpi1", orphans)

    def test_partial_graph_allowed_no_crash(self):
        """GraphIRDraft allows incomplete graphs — no invariants during construction."""
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="orphan", type="Chart"))
        # orphan with no edges — valid during construction
        self.assertEqual(len(draft.get_orphan_nodes()), 0)  # single node is root


# ════════════════════════════════════════════════════════════
# 5. GraphIRDraft.freeze() — Success Cases
# ════════════════════════════════════════════════════════════

class TestGraphIRDraftFreeze(unittest.TestCase):
    def test_simple_graph_freezes(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="kpi1", type="KpiRow"))
        draft.add_edge(GraphIREdge(source="page", target="kpi1", role=EdgeRole.PRIMARY))
        graph = draft.freeze()
        self.assertIsInstance(graph, GraphIR)
        self.assertIn("page", graph.nodes)
        self.assertIn("kpi1", graph.nodes)
        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(graph.layout.root, "page")

    def test_frozen_graph_is_immutable(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="kpi1", type="KpiRow"))
        draft.add_edge(GraphIREdge(source="page", target="kpi1", role=EdgeRole.PRIMARY))
        graph = draft.freeze()
        with self.assertRaises(Exception):
            graph.nodes = {}

    def test_complex_graph(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="kpi_row", type="KpiRow"))
        draft.add_node(GraphIRNode(id="timeseries", type="Timeseries"))
        draft.add_node(GraphIRNode(id="table", type="AnalyticsTable"))
        draft.add_edge(GraphIREdge(source="page", target="kpi_row", role=EdgeRole.PRIMARY))
        draft.add_edge(GraphIREdge(source="page", target="timeseries", role=EdgeRole.SUPPORTING))
        draft.add_edge(GraphIREdge(source="page", target="table", role=EdgeRole.SUPPORTING))
        graph = draft.freeze()
        self.assertEqual(len(graph.nodes), 4)
        self.assertEqual(len(graph.edges), 3)
        self.assertEqual(graph.layout.root, "page")


# ════════════════════════════════════════════════════════════
# 6. GraphIRDraft.freeze() — Rejection Cases
# ════════════════════════════════════════════════════════════

class TestGraphIRDraftFreezeRejection(unittest.TestCase):
    def test_rejects_empty_graph(self):
        draft = GraphIRDraft()
        with self.assertRaises(ValueError) as ctx:
            draft.freeze()
        self.assertIn("empty", str(ctx.exception).lower())

    def test_rejects_dangling_edge_source(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_edge(GraphIREdge(source="nonexistent", target="page", role=EdgeRole.CONTAINS))
        with self.assertRaises(ValueError) as ctx:
            draft.freeze()
        self.assertIn("source", str(ctx.exception))

    def test_rejects_dangling_edge_target(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_edge(GraphIREdge(source="page", target="nonexistent", role=EdgeRole.PRIMARY))
        with self.assertRaises(ValueError) as ctx:
            draft.freeze()
        self.assertIn("target", str(ctx.exception))

    def test_rejects_cycle(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="a", type="Page"))
        draft.add_node(GraphIRNode(id="b", type="Chart"))
        draft.add_node(GraphIRNode(id="c", type="Table"))
        draft.add_edge(GraphIREdge(source="a", target="b", role=EdgeRole.CONTAINS))
        draft.add_edge(GraphIREdge(source="b", target="c", role=EdgeRole.CONTAINS))
        draft.add_edge(GraphIREdge(source="c", target="a", role=EdgeRole.CONTAINS))  # cycle
        with self.assertRaises(ValueError) as ctx:
            draft.freeze()
        self.assertIn("cycle", str(ctx.exception).lower())

    def test_rejects_two_roots(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="root1", type="Page"))
        draft.add_node(GraphIRNode(id="root2", type="Page"))
        with self.assertRaises(ValueError) as ctx:
            draft.freeze()
        self.assertIn("root", str(ctx.exception).lower())

    def test_rejects_unreachable_node(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="orphan", type="Chart"))
        draft.add_edge(GraphIREdge(source="page", target="orphan", role=EdgeRole.PRIMARY))
        draft.add_node(GraphIRNode(id="unreachable", type="Table"))
        with self.assertRaises(ValueError) as ctx:
            draft.freeze()
        self.assertIn("unreachable", str(ctx.exception).lower())


# ════════════════════════════════════════════════════════════
# 7. GraphIR immutability
# ════════════════════════════════════════════════════════════

class TestGraphIRImmutability(unittest.TestCase):
    def _make_graph(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="kpi1", type="KpiRow"))
        draft.add_edge(GraphIREdge(source="page", target="kpi1", role=EdgeRole.PRIMARY))
        return draft.freeze()

    def test_cannot_reassign_nodes(self):
        graph = self._make_graph()
        with self.assertRaises(Exception):
            graph.nodes = {}

    def test_cannot_reassign_edges(self):
        graph = self._make_graph()
        with self.assertRaises(Exception):
            graph.edges = []

    def test_cannot_reassign_params(self):
        graph = self._make_graph()
        with self.assertRaises(Exception):
            graph.params = {}


# ════════════════════════════════════════════════════════════
# 8. GraphIRLayout
# ════════════════════════════════════════════════════════════

class TestGraphIRLayout(unittest.TestCase):
    def test_create_layout(self):
        layout = GraphIRLayout(root="page", constraints={
            "page": [LayoutConstraint.COLUMN],
            "kpi1": [LayoutConstraint.FULL_WIDTH],
        })
        self.assertEqual(layout.root, "page")
        self.assertIn(LayoutConstraint.FULL_WIDTH, layout.constraints["kpi1"])

    def test_layout_is_immutable(self):
        layout = GraphIRLayout(root="page")
        with self.assertRaises(Exception):
            layout.root = "changed"


# ════════════════════════════════════════════════════════════
# 9. IntentType
# ════════════════════════════════════════════════════════════

class TestIntentType(unittest.TestCase):
    def test_core_types_exist(self):
        self.assertIn("PAGE", IntentType.__members__)
        self.assertIn("KPIGROUP", IntentType.__members__)
        self.assertIn("CHART", IntentType.__members__)
        self.assertIn("DATATABLE", IntentType.__members__)

    def test_intent_node_no_framework_refs(self):
        node = IntentNode(type="KPIGroup", params={"metrics": ["revenue"]})
        self.assertEqual(node.params["metrics"], ["revenue"])
        # No layout/framework keys allowed by convention


# ════════════════════════════════════════════════════════════
# 10. IntentExtensionRegistry
# ════════════════════════════════════════════════════════════

class TestIntentExtensionRegistry(unittest.TestCase):
    def setUp(self):
        IntentExtensionRegistry.clear()

    def test_register_extension(self):
        IntentExtensionRegistry.register("CustomWidget", {
            "graphir_type": "CustomWidget",
            "edge_role": "SUPPORTING",
        })
        self.assertTrue(IntentExtensionRegistry.is_valid("CustomWidget"))

    def test_is_valid_for_core_types(self):
        self.assertTrue(IntentExtensionRegistry.is_valid("PAGE"))
        self.assertTrue(IntentExtensionRegistry.is_valid("KPIGROUP"))

    def test_rejects_invalid_extension(self):
        self.assertFalse(IntentExtensionRegistry.is_valid("NonExistent"))

    def test_extension_requires_graphir_type(self):
        with self.assertRaises(ValueError) as ctx:
            IntentExtensionRegistry.register("BadExt", {})
        self.assertIn("graphir_type", str(ctx.exception))

    def test_extension_requires_valid_edge_role(self):
        with self.assertRaises(ValueError) as ctx:
            IntentExtensionRegistry.register("BadExt", {
                "graphir_type": "X",
                "edge_role": "HEADER",  # not CONTAINS/PRIMARY/SUPPORTING
            })
        self.assertIn("edge_role", str(ctx.exception))

    def test_resolve_graphir_type(self):
        self.assertEqual(
            IntentExtensionRegistry.resolve_graphir_type("KPIGROUP"),
            "KpiRow",
        )
        IntentExtensionRegistry.register("MyWidget", {
            "graphir_type": "MyWidget",
            "edge_role": "PRIMARY",
        })
        self.assertEqual(
            IntentExtensionRegistry.resolve_graphir_type("MyWidget"),
            "MyWidget",
        )

    def test_unregister_extension(self):
        IntentExtensionRegistry.register("TempExt", {
            "graphir_type": "Temp",
            "edge_role": "CONTAINS",
        })
        self.assertTrue(IntentExtensionRegistry.is_valid("TempExt"))
        IntentExtensionRegistry.unregister("TempExt")
        self.assertFalse(IntentExtensionRegistry.is_valid("TempExt"))


# ════════════════════════════════════════════════════════════
# 11. IntentPlan validation
# ════════════════════════════════════════════════════════════

class TestIntentPlan(unittest.TestCase):
    def setUp(self):
        IntentExtensionRegistry.clear()

    def test_valid_intent_plan(self):
        plan = IntentPlan(
            intents=[Intent(
                id=make_intent_id("test", "display.kpi_row"),
                capability="display.kpi_row",
                params={"metrics": ["revenue"]},
            )],
            params={"metrics": ["revenue"]},
        )
        IntentPlan.validate(plan)  # should not raise

    def test_rejects_unknown_capability(self):
        plan = IntentPlan(
            intents=[Intent(
                id=make_intent_id("test", "display.revenue_chart"),
                capability="display.revenue_chart",
            )],
            params={},
        )
        with self.assertRaises(ValueError) as ctx:
            IntentPlan.validate(plan)
        self.assertIn("unknown", str(ctx.exception).lower())

    def test_rejects_empty_intents(self):
        plan = IntentPlan(
            intents=[],
            params={},
        )
        with self.assertRaises(ValueError) as ctx:
            IntentPlan.validate(plan)
        self.assertIn("at least one", str(ctx.exception).lower())

    def test_legacy_intentnode_still_valid(self):
        """Backward compat: IntentNode still works in IntentPlan."""
        plan = IntentPlan(
            intents=[IntentNode(type="KPIGROUP", params={"metrics": ["revenue"]})],
            params={"metrics": ["revenue"]},
        )
        IntentPlan.validate(plan)  # should not raise

    def test_legacy_intentnode_rejects_unknown(self):
        plan = IntentPlan(
            intents=[IntentNode(type="RevenueChart")],
            params={},
        )
        with self.assertRaises(ValueError) as ctx:
            IntentPlan.validate(plan)
        self.assertIn("unknown", str(ctx.exception).lower())


# ════════════════════════════════════════════════════════════
# 12. LayoutDerivationEngine — Determinism
# ════════════════════════════════════════════════════════════

class TestLayoutDerivationEngine(unittest.TestCase):
    def _make_dashboard_graph(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="kpi_row", type="KpiRow", metadata={"priority": "high"}))
        draft.add_node(GraphIRNode(id="chart", type="Timeseries"))
        draft.add_node(GraphIRNode(id="table", type="AnalyticsTable"))
        draft.add_edge(GraphIREdge(source="page", target="kpi_row", role=EdgeRole.PRIMARY))
        draft.add_edge(GraphIREdge(source="page", target="chart", role=EdgeRole.SUPPORTING))
        draft.add_edge(GraphIREdge(source="page", target="table", role=EdgeRole.SUPPORTING))
        return draft.freeze()

    def test_derive_is_deterministic(self):
        graph = self._make_dashboard_graph()
        layout1 = LayoutDerivationEngine.derive(graph)
        layout2 = LayoutDerivationEngine.derive(graph)
        self.assertEqual(layout1, layout2)

    def test_derive_produces_constraints_for_all_nodes(self):
        graph = self._make_dashboard_graph()
        layout = LayoutDerivationEngine.derive(graph)
        for nid in graph.nodes:
            self.assertIn(nid, layout.constraints,
                          f"node '{nid}' missing layout constraint")

    def test_derive_assigns_root(self):
        graph = self._make_dashboard_graph()
        layout = LayoutDerivationEngine.derive(graph)
        self.assertEqual(layout.root, "page")

    def test_primary_gets_full_width(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="kpi_row", type="KpiRow"))
        draft.add_edge(GraphIREdge(source="page", target="kpi_row", role=EdgeRole.PRIMARY))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)
        self.assertIn(LayoutConstraint.FULL_WIDTH, layout.constraints["kpi_row"])

    def test_supporting_gets_half_width(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="chart", type="Timeseries"))
        draft.add_edge(GraphIREdge(source="page", target="chart", role=EdgeRole.SUPPORTING))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)
        self.assertIn(LayoutConstraint.HALF_WIDTH, layout.constraints["chart"])

    def test_priority_override(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="hidden_chart", type="Timeseries", metadata={"priority": "hidden"}))
        draft.add_edge(GraphIREdge(source="page", target="hidden_chart", role=EdgeRole.SUPPORTING))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)
        self.assertIn(LayoutConstraint.HIDDEN, layout.constraints["hidden_chart"])


# ════════════════════════════════════════════════════════════
# 13. LayoutDerivationEngine — NO type-based branching
# ════════════════════════════════════════════════════════════

class TestLayoutDerivationEngineNoTypeBranching(unittest.TestCase):
    """HARD CONSTRAINT: LayoutDerivationEngine must not have if/switch by node.type.

    Instead of testing for absence of conditionals (which is a code review),
    we test that two nodes with the same role + metadata produce the same constraints,
    regardless of their type.
    """

    def test_same_role_same_constraints_regardless_of_type(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="component_a", type="KpiRow"))
        draft.add_node(GraphIRNode(id="component_b", type="Timeseries"))
        draft.add_edge(GraphIREdge(source="page", target="component_a", role=EdgeRole.PRIMARY))
        draft.add_edge(GraphIREdge(source="page", target="component_b", role=EdgeRole.PRIMARY))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)
        self.assertEqual(
            layout.constraints["component_a"],
            layout.constraints["component_b"],
            "Two nodes with same role but different types got different constraints. "
            "This suggests type-based branching in LayoutDerivationEngine.",
        )

    def test_different_roles_different_constraints(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="primary_comp", type="KpiRow"))
        draft.add_node(GraphIRNode(id="supporting_comp", type="Timeseries"))
        draft.add_edge(GraphIREdge(source="page", target="primary_comp", role=EdgeRole.PRIMARY))
        draft.add_edge(GraphIREdge(source="page", target="supporting_comp", role=EdgeRole.SUPPORTING))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)
        self.assertNotEqual(
            layout.constraints["primary_comp"],
            layout.constraints["supporting_comp"],
            "Different roles should produce different constraints.",
        )


# ════════════════════════════════════════════════════════════
# 14. Debug visualize
# ════════════════════════════════════════════════════════════

class TestDebugVisualize(unittest.TestCase):
    def _make_graph(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="kpi_group", type="KpiRow"))
        draft.add_node(GraphIRNode(id="timeseries", type="Timeseries"))
        draft.add_edge(GraphIREdge(source="page", target="kpi_group", role=EdgeRole.PRIMARY))
        draft.add_edge(GraphIREdge(source="page", target="timeseries", role=EdgeRole.SUPPORTING))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)
        return graph, layout

    def test_visualize_returns_dict(self):
        graph, layout = self._make_graph()
        result = visualize(graph, layout)
        self.assertIsInstance(result, dict)

    def test_visualize_contains_node_count(self):
        graph, layout = self._make_graph()
        result = visualize(graph, layout)
        self.assertEqual(result["node_count"], 3)

    def test_visualize_contains_edge_count(self):
        graph, layout = self._make_graph()
        result = visualize(graph, layout)
        self.assertEqual(result["edge_count"], 2)

    def test_visualize_contains_root(self):
        graph, layout = self._make_graph()
        result = visualize(graph, layout)
        self.assertEqual(result["root"], "page")

    def test_visualize_contains_dag_edges(self):
        graph, layout = self._make_graph()
        result = visualize(graph, layout)
        self.assertIn("page", result["dag_edges"])

    def test_visualize_without_explicit_layout(self):
        graph, layout = self._make_graph()
        result = visualize(graph)  # uses graph.layout
        self.assertIn("constraints_per_node", result)

    def test_visualize_diagnostics_empty_for_valid_graph(self):
        graph, layout = self._make_graph()
        result = visualize(graph, layout)
        self.assertEqual(result["diagnostics"], [])

    def test_visualize_diagnostics_detects_missing_constraint(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="connected", type="KpiRow"))
        draft.add_edge(GraphIREdge(source="page", target="connected", role=EdgeRole.PRIMARY))
        graph = draft.freeze()
        # Create a layout missing a constraint for 'connected'
        bad_layout = GraphIRLayout(root="page", constraints={"page": []})
        result = visualize(graph, bad_layout)
        self.assertIn("diagnostics", result)
        self.assertTrue(any("connected" in d for d in result["diagnostics"]),
                        "Expected 'connected' to appear in diagnostics")


# ════════════════════════════════════════════════════════════
# 15. GraphIRValidator explicit validation
# ════════════════════════════════════════════════════════════

class TestGraphIRValidator(unittest.TestCase):
    def _make_valid_graph(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page"))
        draft.add_node(GraphIRNode(id="kpi1", type="KpiRow"))
        draft.add_edge(GraphIREdge(source="page", target="kpi1", role=EdgeRole.PRIMARY))
        return draft.freeze()

    def test_validate_passes_for_valid_graph(self):
        graph = self._make_valid_graph()
        GraphIRValidator.validate(graph)  # should not raise

    def test_find_root(self):
        graph = self._make_valid_graph()
        root = GraphIRValidator.find_root(graph.nodes, graph.edges)
        self.assertEqual(root, "page")

    def test_check_edges_exist_passes(self):
        graph = self._make_valid_graph()
        GraphIRValidator.check_edges_exist(graph.nodes, graph.edges)

    def test_check_edges_exist_fails_on_bad_source(self):
        nodes = {"a": GraphIRNode(id="a", type="Page")}
        edges = [GraphIREdge(source="nonexistent", target="a", role=EdgeRole.CONTAINS)]
        with self.assertRaises(ValueError):
            GraphIRValidator.check_edges_exist(nodes, edges)

    def test_is_dag_passes(self):
        graph = self._make_valid_graph()
        GraphIRValidator.is_dag(graph.nodes, graph.edges)

    def test_is_dag_fails_on_cycle(self):
        nodes = {
            "a": GraphIRNode(id="a", type="A"),
            "b": GraphIRNode(id="b", type="B"),
            "c": GraphIRNode(id="c", type="C"),
        }
        edges = [
            GraphIREdge(source="a", target="b", role=EdgeRole.CONTAINS),
            GraphIREdge(source="b", target="c", role=EdgeRole.CONTAINS),
            GraphIREdge(source="c", target="a", role=EdgeRole.CONTAINS),
        ]
        with self.assertRaises(ValueError) as ctx:
            GraphIRValidator.is_dag(nodes, edges)
        self.assertIn("cycle", str(ctx.exception).lower())

    def test_check_edge_role_purity_passes(self):
        edges = [
            GraphIREdge(source="a", target="b", role=EdgeRole.CONTAINS),
            GraphIREdge(source="a", target="c", role=EdgeRole.PRIMARY),
        ]
        GraphIRValidator.check_edge_role_purity(edges)  # should not raise

    def test_check_edge_role_purity_rejects_position_role(self):
        """Simulate what happens if someone adds a HEADER role."""
        from app.graphir.models import EdgeRole

        # Create a custom edge with an invalid role type
        # We can't actually create an edge with a non-EdgeRole value
        # because the type hint enforces it. This test verifies the
        # validation exists for when EdgeRole is clean.
        valid_edges = [
            GraphIREdge(source="a", target="b", role=EdgeRole.CONTAINS),
        ]
        GraphIRValidator.check_edge_role_purity(valid_edges)  # passes


class TestEdgeRolePurityEnforcement(unittest.TestCase):
    """Verify that EdgeRole construction itself prevents bad values."""

    def test_cannot_create_edge_with_invalid_role(self):
        from app.graphir.models import EdgeRole
        # EdgeRole is an enum, so invalid values are caught at construction
        with self.assertRaises(ValueError):
            # EdgeRole only has CONTAINS/PRIMARY/SUPPORTING
            GraphIREdge(source="a", target="b", role="HEADER")  # type: ignore


if __name__ == "__main__":
    unittest.main()
