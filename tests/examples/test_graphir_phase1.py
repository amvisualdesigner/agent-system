"""Integration tests for GraphIR Phase 1: Builder + Pipeline + Renderer.

Test coverage:
  1. GraphIRBuilder — IntentPlan → GraphIR
  2. GraphIRPipeline — IntentPlan → (GraphIR, GraphIRLayout)
  3. GraphIRRenderer — GraphIR + GraphIRLayout → FileOps
  4. End-to-end: IntentPlan → rendered files
  5. Edge cases: single intent, orphan auto-attach, unknown types
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.intent import (
    IntentType,
    IntentExtensionRegistry,
    IntentNode,
    IntentPlan,
)
from app.graphir.models import (
    EdgeRole,
    LayoutConstraint,
    GraphIRNode,
    GraphIREdge,
    GraphIR,
    GraphIRDraft,
)
from app.graphir.builder import GraphIRBuilder, build_from_plan
from app.graphir.pipeline import GraphIRPipeline, run_pipeline
from app.graphir.layout import LayoutDerivationEngine
from app.graphir.validator import GraphIRValidator
from app.graphir.backends import ReactBackend, BackendConfig


class TestGraphIRBuilder(unittest.TestCase):
    """GraphIRBuilder: IntentPlan → GraphIR via GraphIRDraft."""

    def setUp(self):
        IntentExtensionRegistry.clear()

    def _make_plan(self):
        return IntentPlan(
            contract_id="dashboard.sales_overview",
            version=1,
            confidence=0.9,
            intents=[
                IntentNode(type="PAGE", params={}),
                IntentNode(type="KPIGROUP", params={"metrics": ["revenue", "growth"]}),
                IntentNode(type="CHART", params={"metric": "revenue"}),
            ],
            params={"metrics": ["revenue", "growth"]},
        )

    def test_build_returns_graphir(self):
        plan = self._make_plan()
        graph = GraphIRBuilder.build(plan)
        self.assertIsInstance(graph, GraphIR)
        self.assertTrue(len(graph.nodes) >= 1)

    def test_first_intent_is_root(self):
        plan = self._make_plan()
        graph = GraphIRBuilder.build(plan)
        self.assertEqual(graph.layout.root, graph.nodes["Page"].id)

    def test_root_has_no_incoming_edges(self):
        plan = self._make_plan()
        graph = GraphIRBuilder.build(plan)
        targets = {e.target for e in graph.edges}
        self.assertNotIn(graph.layout.root, targets)

    def test_child_nodes_have_incoming_edges(self):
        plan = self._make_plan()
        graph = GraphIRBuilder.build(plan)
        targets = {e.target for e in graph.edges}
        for nid, node in graph.nodes.items():
            if nid != graph.layout.root:
                self.assertIn(nid, targets,
                              f"child '{nid}' has no incoming edge")

    def test_all_nodes_reachable_from_root(self):
        plan = self._make_plan()
        graph = GraphIRBuilder.build(plan)
        GraphIRValidator.is_dag(graph.nodes, graph.edges)  # also checks reachability

    def test_single_intent_produces_single_node(self):
        plan = IntentPlan(
            contract_id="analytics.table",
            version=1,
            confidence=0.8,
            intents=[IntentNode(type="DATATABLE", params={"columns": ["col1"]})],
            params={},
        )
        graph = GraphIRBuilder.build(plan)
        self.assertEqual(len(graph.nodes), 1)

    def test_rejects_unknown_intent_type(self):
        plan = IntentPlan(
            contract_id="test",
            version=1,
            confidence=0.5,
            intents=[IntentNode(type="NonExistentType", params={})],
            params={},
        )
        with self.assertRaises(ValueError):
            GraphIRBuilder.build(plan)

    def test_build_from_plan_convenience(self):
        plan = self._make_plan()
        graph = build_from_plan(plan)
        self.assertIsInstance(graph, GraphIR)


class TestGraphIRPipeline(unittest.TestCase):
    """GraphIRPipeline: IntentPlan → (GraphIR, GraphIRLayout)."""

    def setUp(self):
        IntentExtensionRegistry.clear()

    def _make_plan(self):
        return IntentPlan(
            contract_id="dashboard.sales_overview",
            version=1,
            confidence=0.9,
            intents=[
                IntentNode(type="PAGE", params={}),
                IntentNode(type="KPIGROUP", params={"metrics": ["revenue"]}),
                IntentNode(type="CHART", params={"metric": "revenue"}),
            ],
            params={"metrics": ["revenue"]},
        )

    def test_run_returns_tuple(self):
        plan = self._make_plan()
        graph, layout = GraphIRPipeline.run(plan)
        self.assertIsInstance(graph, GraphIR)
        self.assertIsInstance(layout, type(graph.layout))

    def test_layout_root_matches_graph_root(self):
        plan = self._make_plan()
        graph, layout = GraphIRPipeline.run(plan)
        self.assertEqual(layout.root, graph.layout.root)

    def test_every_node_has_constraint(self):
        plan = self._make_plan()
        _, layout = GraphIRPipeline.run(plan)
        for nid in layout.constraints:
            self.assertTrue(
                len(layout.constraints[nid]) > 0,
                f"node '{nid}' has no constraints",
            )

    def test_pipeline_deterministic(self):
        plan = self._make_plan()
        g1, l1 = GraphIRPipeline.run(plan)
        g2, l2 = GraphIRPipeline.run(plan)
        self.assertEqual(l1, l2)

    def test_run_pipeline_convenience(self):
        plan = self._make_plan()
        graph, layout = run_pipeline(plan)
        self.assertIsInstance(graph, GraphIR)


class TestReactBackend(unittest.TestCase):
    """ReactBackend: GraphIR + GraphIRLayout → FileOps."""

    def setUp(self):
        IntentExtensionRegistry.clear()

    def _build_graph(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="Page", type="Page", data={}))
        draft.add_node(GraphIRNode(id="KpiRow_0", type="KpiRow",
                                    data={"metrics": ["revenue", "growth"]}))
        draft.add_node(GraphIRNode(id="Timeseries_1", type="Timeseries",
                                    data={"metric": "revenue"}))
        draft.add_edge(GraphIREdge(source="Page", target="KpiRow_0",
                                    role=EdgeRole.PRIMARY))
        draft.add_edge(GraphIREdge(source="Page", target="Timeseries_1",
                                    role=EdgeRole.SUPPORTING))
        return draft.freeze()

    def _make_backend(self, base_path="src/pages/dashboard/", path_map=None):
        if path_map is None:
            path_map = {"Page": "Page.tsx", "KpiRow": "components/KpiRow.tsx",
                        "Timeseries": "components/Timeseries.tsx"}
        config = BackendConfig(output_base_path=base_path, path_map=path_map)
        return ReactBackend(), config

    def test_render_returns_fileops(self):
        graph = self._build_graph()
        layout = LayoutDerivationEngine.derive(graph)
        backend, config = self._make_backend()
        fileops = backend.render(graph, layout, config)
        self.assertIsInstance(fileops, list)
        self.assertTrue(len(fileops) > 0)

    def test_render_produces_all_files(self):
        graph = self._build_graph()
        layout = LayoutDerivationEngine.derive(graph)
        backend, config = self._make_backend()
        fileops = backend.render(graph, layout, config)
        paths = [fop.path for fop in fileops]
        self.assertIn("src/pages/dashboard/Page.tsx", paths)
        self.assertIn("src/pages/dashboard/components/KpiRow.tsx", paths)
        self.assertIn("src/pages/dashboard/components/Timeseries.tsx", paths)

    def test_render_content_contains_component_names(self):
        graph = self._build_graph()
        layout = LayoutDerivationEngine.derive(graph)
        backend, config = self._make_backend()
        fileops = backend.render(graph, layout, config)
        for fop in fileops:
            comp_name = fop.path.split("/")[-1].replace(".tsx", "")
            self.assertIn(comp_name, fop.content)

    def test_root_renders_children(self):
        graph = self._build_graph()
        layout = LayoutDerivationEngine.derive(graph)
        backend, config = self._make_backend()
        fileops = backend.render(graph, layout, config)
        root_fop = next(f for f in fileops if "Page" in f.path)
        self.assertIn("KpiRow", root_fop.content)
        self.assertIn("Timeseries", root_fop.content)

    def test_skips_unregistered_node_types(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="Page", type="Page", data={}))
        draft.add_node(GraphIRNode(id="Extra", type="ExtraComponent", data={}))
        draft.add_edge(GraphIREdge(source="Page", target="Extra", role=EdgeRole.CONTAINS))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)
        backend, config = self._make_backend(base_path="src/",
                                              path_map={"Page": "Page.tsx"})
        fileops = backend.render(graph, layout, config)
        self.assertEqual(len(fileops), 1)
        self.assertIn("Page.tsx", fileops[0].path)

    def test_analytics_table_generated(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="AnalyticsTable_0", type="AnalyticsTable",
                                    data={"columns": ["col1", "col2"]}))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)
        backend = ReactBackend()
        config = BackendConfig(output_base_path="src/",
                                path_map={"AnalyticsTable": "AnalyticsTable.tsx"})
        fileops = backend.render(graph, layout, config)
        self.assertEqual(len(fileops), 1)
        self.assertIn("columns.map", fileops[0].content)
        self.assertIn("columns: string[]", fileops[0].content)

    def test_no_baked_values_in_runtime(self):
        graph = self._build_graph()
        layout = LayoutDerivationEngine.derive(graph)
        backend, config = self._make_backend()
        fileops = backend.render(graph, layout, config)
        for fop in fileops:
            if "Timeseries" in fop.path:
                self.assertIn("{metric}", fop.content,
                              "Timeseries debe usar {metric} runtime, no baked value")
            if "KpiRow" in fop.path:
                self.assertIn("{metrics.map", fop.content,
                              "KpiRow debe usar {metrics.map(...)} runtime, no Python loop")


class TestGraphIREndToEnd(unittest.TestCase):
    """End-to-end: IntentPlan → rendered FileOps via ReactBackend."""

    def setUp(self):
        IntentExtensionRegistry.clear()

    def test_full_pipeline_to_fileops(self):
        plan = IntentPlan(
            contract_id="dashboard.sales_overview",
            version=1,
            confidence=0.9,
            intents=[
                IntentNode(type="PAGE", params={}),
                IntentNode(type="KPIGROUP", params={"metrics": ["revenue"]}),
            ],
            params={"metrics": ["revenue"]},
        )
        graph, layout = GraphIRPipeline.run(plan)
        backend = ReactBackend()
        config = BackendConfig(output_base_path="src/",
                                path_map={"Page": "Page.tsx", "KpiRow": "KpiRow.tsx"})
        fileops = backend.render(graph, layout, config)
        self.assertEqual(len(fileops), 2)

    def test_fileops_have_content(self):
        plan = IntentPlan(
            contract_id="analytics.table",
            version=1,
            confidence=0.9,
            intents=[IntentNode(type="DATATABLE",
                                 params={"columns": ["col1", "col2"]})],
            params={},
        )
        graph, layout = GraphIRPipeline.run(plan)
        backend = ReactBackend()
        config = BackendConfig(output_base_path="src/",
                                path_map={"AnalyticsTable": "AnalyticsTable.tsx"})
        fileops = backend.render(graph, layout, config)
        self.assertEqual(len(fileops), 1)
        self.assertIn("columns.map", fileops[0].content)
        self.assertIn("columns: string[]", fileops[0].content)

    def test_multiple_intents_produce_valid_dag(self):
        plan = IntentPlan(
            contract_id="dashboard.multi",
            version=1,
            confidence=0.8,
            intents=[
                IntentNode(type="PAGE", params={}),
                IntentNode(type="KPIGROUP", params={"metrics": ["a", "b"]}),
                IntentNode(type="CHART", params={"metric": "a"}),
                IntentNode(type="DATATABLE", params={"columns": ["x", "y"]}),
                IntentNode(type="FILTERPANEL", params={"filters": ["date"]}),
            ],
            params={},
        )
        graph = GraphIRBuilder.build(plan)
        GraphIRValidator.validate(graph)
        self.assertEqual(len(graph.nodes), 5)
        self.assertEqual(len(graph.edges), 4)


if __name__ == "__main__":
    unittest.main()
