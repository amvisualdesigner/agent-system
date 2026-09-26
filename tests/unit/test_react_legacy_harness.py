"""LEGACY-HARNESS — coverage for SHARED ReactBackend generators.

F10/F11 (option 3): the legacy `ReactBackend.render()` / `render_tree()`
entry points were removed from production. This file preserves the still-valid
coverage of the SHARED component generators (the `ReactBackend._generators`
registry) that the ConstraintGraph ContentGenerator delegates to at
`constraint/generator.py:56-61`.

This harness does NOT recreate render()/render_tree() under another name and
does NOT keep an executable legacy route. It exercises the exact same direct
call the production ContentGenerator uses:

    generator = ReactBackend._generators[node.type]
    generator(node, layout.constraints.get(node.id, []), ReactBackend(), config)

The two failures below are PRE-EXISTING (they fail identically in the old
test_generator_content.py baseline) — they are kept verbatim, not "fixed",
to keep the historical baseline comparison honest.

Note: this is a HARNESS for generator content only. Composition, page hooks,
binding emission and export normalization are covered by the constraint
renderer tests (test_real_repo_integration, test_full_pipeline,
test_composition_audit, golden tests) on the live materialization route.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.models import GraphIRLayout, GraphIRNode
from app.graphir.backends.base import BackendConfig
from app.graphir.backends.react_backend import ReactBackend
from app.graphir.constraint.generator import ContentGenerator


def _generate(node_type: str, props: dict, config: BackendConfig) -> str:
    """Direct shared-generator invocation — same path ContentGenerator uses.

    Note: this mirror call is deliberate. It is NOT a recreation of the
    removed render()/render_tree() — no UIIR compilation, no composition,
    no FileOps, no traces. It produces a single component's content.
    """
    generator = ContentGenerator()
    node = GraphIRNode(id=f"{node_type.lower()}1", type=node_type, data=props)
    layout = GraphIRLayout(root="", constraints={})
    return generator.generate(node, layout, config)


class TestGeneratorContent(unittest.TestCase):
    """Each test verifies one shared generator produces real content."""

    maxDiff = None

    def _render(self, node_type: str, props: dict) -> str:
        return _generate(node_type, props, BackendConfig(output_base_path="src/"))

    # ── 12 generators ─────────────────────────────────────────────

    def test_page_generator_produces_layout_wrapper(self):
        # Page emits the __COMPOSITION__ placement hook by design; the
        # constraint renderer materializes it (covered by
        # tests/e2e/test_composition_audit.py). Not a generator concern.
        content = self._render("Page", {})
        self.assertIn("export const Page: React.FC", content)
        self.assertIn("<div>", content)
        self.assertIn("</div>", content)
        self.assertIn("__COMPOSITION__", content)

    def test_kpi_row_generator_produces_metrics_iteration(self):
        # PRE-EXISTING FAILURE (verified in test_generator_content.py baseline):
        # asserts "metrics:" but the fallback template declares `data:`.
        content = self._render("KpiRow", {"metrics": ["revenue", "users"]})
        self.assertIn("KpiRowProps", content)
        self.assertIn("metrics:", content)
        self.assertIn("kpi-row", content)
        self.assertIn("kpi-card", content)

    def test_timeseries_generator_produces_metric_chart(self):
        # PRE-EXISTING FAILURE (verified in test_generator_content.py baseline):
        # asserts "metric:" but the fallback template declares `data:`.
        content = self._render("Timeseries", {"metric": "revenue"})
        self.assertIn("TimeseriesProps", content)
        self.assertIn("metric:", content)
        self.assertIn("timeseries-chart", content)

    def test_analytics_table_generator_produces_table(self):
        content = self._render("AnalyticsTable", {"columns": ["A", "B"], "rows": []})
        self.assertIn("AnalyticsTableProps", content)
        self.assertIn("columns:", content)
        self.assertIn("analytics-table", content)
        self.assertIn("<table", content)

    def test_filter_panel_generator_produces_checkboxes(self):
        content = self._render("FilterPanel", {"filters": ["active", "inactive"]})
        self.assertIn("FilterPanelProps", content)
        self.assertIn("useState", content)
        self.assertIn("filter-chip", content)
        self.assertIn('type="checkbox"', content)

    def test_bar_chart_generator_produces_bars(self):
        content = self._render("BarChart", {"categories": ["A", "B"], "values": [10, 20]})
        self.assertIn("BarChartProps", content)
        self.assertIn("bar-fill", content)
        self.assertIn("bar-track", content)
        self.assertIn("bar-label", content)

    def test_metric_card_generator_produces_card(self):
        content = self._render("MetricCard", {"value": 42, "label": "Revenue"})
        self.assertIn("MetricCardProps", content)
        self.assertIn("metric-value", content)
        self.assertIn("metric-label", content)

    def test_embed_generator_produces_iframe(self):
        content = self._render("Embed", {"src": "https://example.com", "title": "Demo"})
        self.assertIn("EmbedProps", content)
        self.assertIn("<iframe", content)
        self.assertIn("embed-container", content)

    def test_search_bar_generator_produces_input(self):
        content = self._render("SearchBar", {"placeholder": "Search..."})
        self.assertIn("SearchBarProps", content)
        self.assertIn("useState", content)
        self.assertIn('type="text"', content)
        self.assertIn("Search", content)

    def test_form_generator_produces_dynamic_form(self):
        content = self._render(
            "Form", {"fields": [{"label": "Name", "key": "name", "type": "text"}]},
        )
        self.assertIn("FormProps", content)
        self.assertIn("useState", content)
        self.assertIn("form-component", content)
        self.assertIn("Submit", content)

    def test_export_button_generator_produces_button(self):
        content = self._render("ExportButton", {"format": "csv"})
        self.assertIn("ExportButtonProps", content)
        self.assertIn("Export as", content)
        self.assertIn("onExport", content)

    def test_drilldown_generator_produces_anchor(self):
        content = self._render("Drilldown", {"label": "Details", "target": "/details"})
        self.assertIn("DrilldownProps", content)
        self.assertIn("drilldown-link", content)
        self.assertIn("View details", content)


class TestSignatureOverridePath(unittest.TestCase):
    """Signature override enriches generated output with correct types
    while the generator provides the implementation body (destructured
    props, real JSX). No stub markers should appear."""

    def _make_signature(self, iface_name: str, props_body: str) -> dict:
        return {
            "props": f"interface {iface_name} {{\n  {props_body}\n}}",
            "imports": ["import React from 'react';"],
        }

    def test_signature_override_uses_generator_destructure(self):
        """Signature path uses signature interface but generator's destructured props."""
        sig = self._make_signature("TimeseriesProps", "metric: string; title?: string;")
        config = BackendConfig(
            output_base_path="src/",
            component_signatures={"Timeseries": sig},
        )
        content = _generate("Timeseries", {}, config)
        self.assertIn("TimeseriesProps", content)
        self.assertNotIn("_props", content)
        self.assertIn("{ metric }", content)
        self.assertIn("Card", content)
        self.assertNotIn("__COMPOSITION__", content)

    def test_signature_override_with_extra_types(self):
        """extra_types from signature appear in generated output."""
        sig = self._make_signature("KpiRowProps", "data: KpiItem[];")
        sig["extra_types"] = ["interface KpiItem { label: string; value: number; }"]
        config = BackendConfig(
            output_base_path="src/",
            component_signatures={"KpiRow": sig},
        )
        content = _generate("KpiRow", {}, config)
        self.assertIn("KpiItem", content)
        self.assertNotIn("_props", content)
        self.assertIn("metrics.map", content)

    def test_signature_override_does_not_produce_empty_body(self):
        """Body should contain meaningful content even with signature."""
        sig = self._make_signature("MetricCardProps", "value?: number; label?: string;")
        config = BackendConfig(
            output_base_path="src/",
            component_signatures={"MetricCard": sig},
        )
        content = _generate("MetricCard", {}, config)
        self.assertIn("export const MetricCard: React.FC<MetricCardProps>", content)
        self.assertIn("return", content)
        self.assertNotIn("__COMPOSITION__", content)
        self.assertNotIn("_props", content)

    def test_no_signature_fallback_never_uses_underscore_props(self):
        """Without signature override, hardcoded template uses destructured
        props (e.g. { metric }), not _props."""
        content = _generate("Timeseries", {"metric": "revenue"}, BackendConfig(output_base_path="src/"))
        self.assertNotIn("_props", content)
        self.assertIn("TimeseriesProps", content)
        self.assertIn("metric", content)


# ── Regression: signature override must not produce empty stubs ─────────────

class TestContentRegression(unittest.TestCase):
    """Verify that generated code contains real implementation,
    not stub markers from _render_signature() short-circuit."""

    def _make_sig(self, iface: str, props: str) -> dict:
        return {
            "props": f"interface {iface} {{\n  {props}\n}}",
            "imports": ["import React from 'react';"],
        }

    def test_kpi_row_contains_metric_rendering(self):
        sig = self._make_sig("KpiRowProps", "data: KpiItem[];")
        config = BackendConfig(
            output_base_path="src/",
            component_signatures={"KpiRow": sig},
        )
        content = _generate("KpiRow", {}, config)
        self.assertNotIn("_props", content)
        self.assertNotIn("__COMPOSITION__", content)
        self.assertIn("map(", content)

    def test_timeseries_contains_chart_logic(self):
        sig = self._make_sig("TimeseriesProps", "metric: string;")
        config = BackendConfig(
            output_base_path="src/",
            component_signatures={"Timeseries": sig},
        )
        content = _generate("Timeseries", {}, config)
        self.assertNotIn("_props", content)
        self.assertNotIn("__COMPOSITION__", content)
        has_chart_markup = (
            "svg" in content.lower()
            or "polyline" in content.lower()
            or "Card" in content
        )
        self.assertTrue(has_chart_markup, "No chart/SVG/Card markup found in generated code")


if __name__ == "__main__":
    unittest.main()