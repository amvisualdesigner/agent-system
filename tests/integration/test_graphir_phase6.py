"""Phase 6 tests: Complete end-to-end flow — all 22 capabilities.

Test coverage:
  1. Metadata-only capabilities (domain, style, layout.grid/container)
  2. New GraphIR types (data.*, interaction.*)
  3. Complete pipeline: decompose → validate → build → render
  4. All 12 React generators produce valid output
  5. Contracts cover all presentation/interaction/data capabilities
  6. Full task → GraphIR → FileOp flow
  7. No-regression: existing Phase 1-4 behavior unchanged
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.intent import (
    Intent,
    IntentPlan,
    make_intent_id,
    is_graphir_node_capability,
    is_capability_metadata,
    CAPABILITY_REGISTRY,
    resolve_graphir_type_from_capability,
    resolve_edge_role_from_capability,
)
from app.graphir.intent_decomposition import decompose_task
from app.graphir.builder import GraphIRBuilder
from app.graphir.pipeline import GraphIRPipeline
from app.graphir.backends.react_backend import ReactBackend
from app.graphir.backends.base import BackendConfig
from app.graphir.intent_coverage import IntentCoverageValidator
from app.graphir.intent_governance import detect_orphans
from app.graphir.models import GraphIR
from app.contracts.skill_registry import SKILL_CONTRACTS


# ═══════════════════════════════════════════════════════════════════
# 1. Metadata-only capabilities
# ═══════════════════════════════════════════════════════════════════

class TestMetadataCapabilities(unittest.TestCase):

    def test_domain_analytics_is_metadata(self):
        self.assertTrue(is_capability_metadata("domain.analytics"))

    def test_domain_sales_is_metadata(self):
        self.assertTrue(is_capability_metadata("domain.sales"))

    def test_style_dark_is_metadata(self):
        self.assertTrue(is_capability_metadata("style.theme.dark"))

    def test_layout_grid_is_metadata(self):
        self.assertTrue(is_capability_metadata("layout.grid"))

    def test_presentation_kpi_is_not_metadata(self):
        self.assertFalse(is_capability_metadata("presentation.kpi_row"))

    def test_data_export_is_not_metadata(self):
        self.assertFalse(is_capability_metadata("data.export"))

    def test_unknown_cap_not_metadata(self):
        self.assertFalse(is_capability_metadata("nonexistent.foo"))


# ═══════════════════════════════════════════════════════════════════
# 2. GraphIR node capabilities
# ═══════════════════════════════════════════════════════════════════

class TestNodeCapabilities(unittest.TestCase):

    def test_presentation_are_nodes(self):
        for cap in ["presentation.kpi_row", "presentation.timeseries",
                     "presentation.table", "presentation.filter_panel",
                     "presentation.chart.bar", "presentation.metric_card",
                     "presentation.embed"]:
            self.assertTrue(is_graphir_node_capability(cap), cap)

    def test_data_are_nodes(self):
        for cap in ["data.export", "data.drilldown"]:
            self.assertTrue(is_graphir_node_capability(cap), cap)

    def test_interaction_are_nodes(self):
        for cap in ["interaction.search", "interaction.form"]:
            self.assertTrue(is_graphir_node_capability(cap), cap)

    def test_domain_are_not_nodes(self):
        for cap in ["domain.analytics", "domain.sales"]:
            self.assertFalse(is_graphir_node_capability(cap), cap)

    def test_style_are_not_nodes(self):
        for cap in ["style.theme.dark", "style.theme.light",
                     "style.theme.enterprise", "style.card.elevated"]:
            self.assertFalse(is_graphir_node_capability(cap), cap)

    def test_layout_grid_container_not_nodes(self):
        self.assertFalse(is_graphir_node_capability("layout.grid"))
        self.assertFalse(is_graphir_node_capability("layout.container"))

    def test_all_node_caps_have_graphir_type(self):
        for cap in [
            "presentation.kpi_row", "presentation.timeseries",
            "presentation.table", "presentation.filter_panel",
            "presentation.chart.bar", "presentation.metric_card",
            "presentation.embed",
            "data.export", "data.drilldown",
            "interaction.search", "interaction.form",
            "layout.page",
        ]:
            self.assertIsNotNone(
                resolve_graphir_type_from_capability(cap), cap
            )
            # Edge role is optional for root-type capabilities (layout.page)


# ═══════════════════════════════════════════════════════════════════
# 3. Complete pipeline: metadata-only intents don't create nodes
# ═══════════════════════════════════════════════════════════════════

class TestPipelineMetadataIntents(unittest.TestCase):

    def test_domain_intent_does_not_create_node(self):
        """domain.sales is metadata-only → no node created."""
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"), capability="layout.page"),
                Intent(id=make_intent_id("kpi", "presentation.kpi_row"), capability="presentation.kpi_row"),
                Intent(id=make_intent_id("sales", "domain.sales"), capability="domain.sales"),
            ],
            params={},
        )
        graph = GraphIRBuilder.build(plan)
        # Only 2 nodes (Page + KpiRow), domain.sales is metadata
        self.assertEqual(len(graph.nodes), 2)
        self.assertIn("domains", graph.params)
        self.assertIn("sales", graph.params["domains"])

    def test_style_intent_sets_style_hints(self):
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"), capability="layout.page"),
                Intent(id=make_intent_id("dark", "style.theme.dark"), capability="style.theme.dark"),
            ],
            params={},
        )
        graph = GraphIRBuilder.build(plan)
        self.assertIn("style_hints", graph.params)
        self.assertTrue(graph.params["style_hints"].get("theme_dark"))

    def test_layout_grid_sets_layout_hints(self):
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"), capability="layout.page"),
                Intent(id=make_intent_id("grid", "layout.grid"), capability="layout.grid"),
            ],
            params={},
        )
        graph = GraphIRBuilder.build(plan)
        self.assertIn("layout_hints", graph.params)
        self.assertTrue(graph.params["layout_hints"].get("grid"))

    def test_mixed_intents_produce_correct_node_count(self):
        """Only node capabilities produce nodes."""
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("p", "layout.page"), capability="layout.page"),
                Intent(id=make_intent_id("k", "presentation.kpi_row"), capability="presentation.kpi_row"),
                Intent(id=make_intent_id("t", "presentation.timeseries"), capability="presentation.timeseries"),
                Intent(id=make_intent_id("d", "domain.analytics"), capability="domain.analytics"),
                Intent(id=make_intent_id("s", "style.theme.dark"), capability="style.theme.dark"),
                Intent(id=make_intent_id("g", "layout.grid"), capability="layout.grid"),
            ],
            params={},
        )
        graph = GraphIRBuilder.build(plan)
        # 3 nodes (Page, KpiRow, Timeseries), 3 metadata
        self.assertEqual(len(graph.nodes), 3)
        self.assertIn("domains", graph.params)
        self.assertIn("analytics", graph.params["domains"])
        self.assertIn("style_hints", graph.params)
        self.assertIn("layout_hints", graph.params)


# ═══════════════════════════════════════════════════════════════════
# 4. New data/interaction capabilities flow through pipeline
# ═══════════════════════════════════════════════════════════════════

class TestNewCapabilityPipeline(unittest.TestCase):

    def _build_graph(self, capabilities: list[str]) -> GraphIR:
        intents = [
            Intent(id=make_intent_id(f"cap_{i}", cap), capability=cap)
            for i, cap in enumerate(capabilities)
        ]
        plan = IntentPlan(intents=intents, params={})
        return GraphIRBuilder.build(plan)

    def test_filter_panel_produces_node(self):
        graph = self._build_graph(["layout.page", "presentation.filter_panel"])
        types = [n.type for n in graph.nodes.values()]
        self.assertIn("FilterPanel", types)

    def test_bar_chart_produces_node(self):
        graph = self._build_graph(["layout.page", "presentation.chart.bar"])
        types = [n.type for n in graph.nodes.values()]
        self.assertIn("BarChart", types)

    def test_metric_card_produces_node(self):
        graph = self._build_graph(["layout.page", "presentation.metric_card"])
        types = [n.type for n in graph.nodes.values()]
        self.assertIn("MetricCard", types)

    def test_embed_produces_node(self):
        graph = self._build_graph(["layout.page", "presentation.embed"])
        types = [n.type for n in graph.nodes.values()]
        self.assertIn("Embed", types)

    def test_search_bar_produces_node(self):
        graph = self._build_graph(["layout.page", "interaction.search"])
        types = [n.type for n in graph.nodes.values()]
        self.assertIn("SearchBar", types)

    def test_form_produces_node(self):
        graph = self._build_graph(["layout.page", "interaction.form"])
        types = [n.type for n in graph.nodes.values()]
        self.assertIn("Form", types)

    def test_export_button_produces_node(self):
        graph = self._build_graph(["layout.page", "data.export"])
        types = [n.type for n in graph.nodes.values()]
        self.assertIn("ExportButton", types)

    def test_drilldown_produces_node(self):
        graph = self._build_graph(["layout.page", "data.drilldown"])
        types = [n.type for n in graph.nodes.values()]
        self.assertIn("Drilldown", types)

    def test_all_types_in_graphir_type_map(self):
        """Every node capability maps to a unique GraphIR type."""
        types_seen = set()
        for cap in [
            "presentation.kpi_row",
            "presentation.timeseries",
            "presentation.table",
            "presentation.filter_panel",
            "presentation.chart.bar",
            "presentation.metric_card",
            "presentation.embed",
            "data.export",
            "data.drilldown",
            "interaction.search",
            "interaction.form",
            "layout.page",
        ]:
            gtype = resolve_graphir_type_from_capability(cap)
            self.assertIsNotNone(gtype)
            self.assertNotIn(gtype, types_seen)  # no duplicate types
            types_seen.add(gtype)


# ═══════════════════════════════════════════════════════════════════
# 5. ReactBackend generates files for all types
# ═══════════════════════════════════════════════════════════════════

class TestReactBackendAllTypes(unittest.TestCase):

    def setUp(self):
        self.backend = ReactBackend()
        self.config = BackendConfig(output_base_path="/tmp/test_out")

    def _build_single_node_graph(self, cap: str, gtype: str) -> GraphIR:
        intent = Intent(id=f"i_{cap}", capability=cap)
        plan = IntentPlan(intents=[
            Intent(id="page", capability="layout.page"),
            intent,
        ], params={})
        return GraphIRBuilder.build(plan)

    def test_all_registered_types_render(self):
        """Every registered generator produces output for all types."""
        for gtype in ReactBackend.registered_types():
            # find a capability that maps to this type
            cap = None
            from app.graphir.intent import _CAPABILITY_TO_GRAPHIR_TYPE
            for c, t in _CAPABILITY_TO_GRAPHIR_TYPE.items():
                if t == gtype:
                    cap = c
                    break
            if cap is None:
                continue
            graph = self._build_single_node_graph(cap, gtype)
            from app.graphir.layout import LayoutDerivationEngine
            layout = LayoutDerivationEngine.derive(graph)
            fileops = self.backend.render(graph, layout, self.config)
            self.assertGreater(len(fileops), 0, f"No output for {gtype}")
            for fop in fileops:
                self.assertEqual(fop.action, "create")
                self.assertIn(gtype, fop.content)

    def test_registered_types_count(self):
        """All 12 generators are registered."""
        types = ReactBackend.registered_types()
        expected = {
            "Page", "KpiRow", "Timeseries", "AnalyticsTable",
            "FilterPanel", "BarChart", "MetricCard", "Embed",
            "SearchBar", "Form", "ExportButton", "Drilldown",
        }
        self.assertEqual(types, expected)


# ═══════════════════════════════════════════════════════════════════
# 6. Contracts cover all node capabilities
# ═══════════════════════════════════════════════════════════════════

class TestContractCoverage(unittest.TestCase):

    def test_every_node_cap_has_contract(self):
        """Every node-creating capability is declared by at least one contract."""
        node_caps = {
            "presentation.kpi_row",
            "presentation.timeseries",
            "presentation.table",
            "presentation.filter_panel",
            "presentation.chart.bar",
            "presentation.metric_card",
            "presentation.embed",
            "data.export",
            "data.drilldown",
            "interaction.search",
            "interaction.form",
            "layout.page",
        }
        contract_caps: set[str] = set()
        for contract in SKILL_CONTRACTS.values():
            ast = contract.ast_template
            caps = (
                ast.get("capabilities", {}).values()
                if isinstance(ast, dict)
                else getattr(ast, "capabilities", {}).values()
            )
            contract_caps.update(caps)

        for cap in node_caps:
            self.assertIn(cap, contract_caps, f"No contract for {cap}")

    def test_domain_cap_in_contract(self):
        """domain.sales is declared by dashboard.sales_overview."""
        contract = SKILL_CONTRACTS[("dashboard.sales_overview", 1)]
        caps = contract.ast_template.get("capabilities", {})
        self.assertIn("Domain", caps)
        self.assertEqual(caps["Domain"], "domain.sales")


# ═══════════════════════════════════════════════════════════════════
# 7. Full end-to-end: decompose → build → render
# ═══════════════════════════════════════════════════════════════════

class TestFullEndToEnd(unittest.TestCase):

    def test_kpi_task_full_flow(self):
        """A complete KPI task flows through the entire pipeline."""
        result = decompose_task("kpi")
        intents = result.intents
        self.assertGreater(len(intents), 0)

        plan = IntentPlan(intents=intents, params={})
        graph = GraphIRBuilder.build(plan)
        self.assertGreater(len(graph.nodes), 0)

        from app.graphir.layout import LayoutDerivationEngine
        layout = LayoutDerivationEngine.derive(graph)

        backend = ReactBackend()
        config = BackendConfig(output_base_path="/tmp/test_out")
        fileops = backend.render(graph, layout, config)
        self.assertGreater(len(fileops), 0)

    def test_full_dashboard_flow(self):
        """Simulate a complete dashboard task."""
        result = decompose_task("dashboard with kpi and timeseries")
        intents = result.intents

        plan = IntentPlan(intents=intents, params={})
        graph = GraphIRBuilder.build(plan)

        from app.graphir.layout import LayoutDerivationEngine
        layout = LayoutDerivationEngine.derive(graph)

        backend = ReactBackend()
        config = BackendConfig(output_base_path="/tmp/test_out")
        fileops = backend.render(graph, layout, config)
        self.assertGreater(len(fileops), 0)

        # Verify Page composition materialized (no placeholder)
        page_file = [f for f in fileops if "Page" in f.path]
        if page_file:
            self.assertNotIn("__COMPOSITION__", page_file[0].content)
            self.assertIn("KpiRow", page_file[0].content)
            self.assertIn("Timeseries", page_file[0].content)

    def test_render_with_style_hints(self):
        """Style hints in graph.params are accessible."""
        plan = IntentPlan(
            intents=[
                Intent(id="page", capability="layout.page"),
                Intent(id="kpi", capability="presentation.kpi_row"),
                Intent(id="dark", capability="style.theme.dark"),
            ],
            params={},
        )
        graph = GraphIRBuilder.build(plan)
        self.assertIn("style_hints", graph.params)

    def test_no_orphans_in_registry(self):
        """No orphan capabilities after Phase 6 additions."""
        report = detect_orphans()
        # All defined capabilities should be referenced somewhere
        # (either as patterns, graphir types, or contracts)
        allowed_orphans = {"domain.analytics"}
        actual_orphans = set(report.orphans) - allowed_orphans
        self.assertEqual(
            actual_orphans, set(),
            f"Unexpected orphans: {actual_orphans}"
        )


# ═══════════════════════════════════════════════════════════════════
# 8. No-regression: existing behavior unchanged
# ═══════════════════════════════════════════════════════════════════

class TestPhase6NoRegression(unittest.TestCase):

    def test_existing_decomposition_unchanged(self):
        result = decompose_task("show kpi", use_embedding=False)
        caps = [i.capability for i in result.intents]
        self.assertIn("presentation.kpi_row", caps)

    def test_builder_still_rejects_unknown(self):
        plan = IntentPlan(
            intents=[Intent(
                id=make_intent_id("test", "unknown.capability"),
                capability="unknown.capability",
            )],
            params={},
        )
        with self.assertRaises(ValueError):
            GraphIRBuilder.build(plan)

    def test_existing_contracts_still_work(self):
        """Existing contracts still resolve correctly."""
        self.assertIn(("dashboard.sales_overview", 1), SKILL_CONTRACTS)
        self.assertIn(("analytics.table", 1), SKILL_CONTRACTS)

    def test_existing_coverage_still_works(self):
        """Basic coverage check still works with new contracts."""
        intents = [
            Intent(id="i1", capability="presentation.kpi_row"),
        ]
        contracts = [
            SKILL_CONTRACTS[("analytics.filter", 1)],
            SKILL_CONTRACTS[("dashboard.sales_overview", 1)],
        ]
        report = IntentCoverageValidator.check_coverage(intents, contracts)
        self.assertGreater(report.coverage, 0)


if __name__ == "__main__":
    unittest.main()
