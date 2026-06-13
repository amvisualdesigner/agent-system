"""Phase 4: Snapshot tests per generator — each of the 12 registered
generators produces real implementation content, not empty stubs.

Every test constructs a UIComponentTree for a single component type and
verifies the generated TSX contains meaningful implementation markers
(props interface, className, actual JSX structure).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.backends.base import BackendConfig
from app.graphir.backends.react_backend import ReactBackend
from app.graphir.ui_ir import UIComponentNode, UIComponentTree


class TestGeneratorContent(unittest.TestCase):
    """Each test verifies one generator produces real content.

    Using render_tree() directly (not the full pipeline) to isolate
    generator behavior from compiler/binding logic.
    """

    maxDiff = None

    def _render(self, node: UIComponentNode) -> str:
        tree = UIComponentTree(root=node)
        config = BackendConfig(output_base_path="src/")
        fileops = ReactBackend().render_tree(tree, config)
        self.assertEqual(len(fileops), 1)
        return fileops[0].content

    # ── 12 generators ─────────────────────────────────────────────

    def test_page_generator_produces_layout_wrapper(self):
        content = self._render(UIComponentNode(
            id="page1", component="Page", props={},
        ))
        self.assertIn("export const Page: React.FC", content)
        self.assertIn("<div>", content)
        self.assertIn("</div>", content)
        self.assertNotIn("__COMPOSITION__", content)

    def test_kpi_row_generator_produces_metrics_iteration(self):
        content = self._render(UIComponentNode(
            id="kpi1", component="KpiRow",
            props={"metrics": ["revenue", "users"]},
        ))
        self.assertIn("KpiRowProps", content)
        self.assertIn("metrics:", content)
        self.assertIn("kpi-row", content)
        self.assertIn("kpi-card", content)

    def test_timeseries_generator_produces_metric_chart(self):
        content = self._render(UIComponentNode(
            id="ts1", component="Timeseries",
            props={"metric": "revenue"},
        ))
        self.assertIn("TimeseriesProps", content)
        self.assertIn("metric:", content)
        self.assertIn("timeseries-chart", content)

    def test_analytics_table_generator_produces_table(self):
        content = self._render(UIComponentNode(
            id="tbl1", component="AnalyticsTable",
            props={"columns": ["A", "B"], "rows": []},
        ))
        self.assertIn("AnalyticsTableProps", content)
        self.assertIn("columns:", content)
        self.assertIn("analytics-table", content)
        self.assertIn("<table", content)

    def test_filter_panel_generator_produces_checkboxes(self):
        content = self._render(UIComponentNode(
            id="fp1", component="FilterPanel",
            props={"filters": ["active", "inactive"]},
        ))
        self.assertIn("FilterPanelProps", content)
        self.assertIn("useState", content)
        self.assertIn("filter-chip", content)
        self.assertIn('type="checkbox"', content)

    def test_bar_chart_generator_produces_bars(self):
        content = self._render(UIComponentNode(
            id="bc1", component="BarChart",
            props={"categories": ["A", "B"], "values": [10, 20]},
        ))
        self.assertIn("BarChartProps", content)
        self.assertIn("bar-fill", content)
        self.assertIn("bar-track", content)
        self.assertIn("bar-label", content)

    def test_metric_card_generator_produces_card(self):
        content = self._render(UIComponentNode(
            id="mc1", component="MetricCard",
            props={"value": 42, "label": "Revenue"},
        ))
        self.assertIn("MetricCardProps", content)
        self.assertIn("metric-value", content)
        self.assertIn("metric-label", content)

    def test_embed_generator_produces_iframe(self):
        content = self._render(UIComponentNode(
            id="emb1", component="Embed",
            props={"src": "https://example.com", "title": "Demo"},
        ))
        self.assertIn("EmbedProps", content)
        self.assertIn("<iframe", content)
        self.assertIn("embed-container", content)

    def test_search_bar_generator_produces_input(self):
        content = self._render(UIComponentNode(
            id="sb1", component="SearchBar",
            props={"placeholder": "Search..."},
        ))
        self.assertIn("SearchBarProps", content)
        self.assertIn("useState", content)
        self.assertIn('type="text"', content)
        self.assertIn("Search", content)

    def test_form_generator_produces_dynamic_form(self):
        content = self._render(UIComponentNode(
            id="fm1", component="Form",
            props={"fields": [{"label": "Name", "key": "name", "type": "text"}]},
        ))
        self.assertIn("FormProps", content)
        self.assertIn("useState", content)
        self.assertIn("form-component", content)
        self.assertIn("Submit", content)

    def test_export_button_generator_produces_button(self):
        content = self._render(UIComponentNode(
            id="eb1", component="ExportButton",
            props={"format": "csv"},
        ))
        self.assertIn("ExportButtonProps", content)
        self.assertIn("Export as", content)
        self.assertIn("onExport", content)

    def test_drilldown_generator_produces_anchor(self):
        content = self._render(UIComponentNode(
            id="dd1", component="Drilldown",
            props={"label": "Details", "target": "/details"},
        ))
        self.assertIn("DrilldownProps", content)
        self.assertIn("drilldown-link", content)
        self.assertIn("View details", content)


class TestSignatureOverridePath(unittest.TestCase):
    """When a component has a signature override, the generated content
    must use the signature interface and _props convention without
    producing empty stubs."""

    def _make_signature(self, iface_name: str, props_body: str) -> dict:
        return {
            "props": f"interface {iface_name} {{\n  {props_body}\n}}",
            "imports": ["import React from 'react';"],
        }

    def test_signature_override_uses_props_convention(self):
        """Signature path produces _props declaration and the interface."""
        sig = self._make_signature("TimeseriesProps", "metric: string; title?: string;")
        config = BackendConfig(
            output_base_path="src/",
            component_signatures={"Timeseries": sig},
        )
        tree = UIComponentTree(root=UIComponentNode(
            id="ts1", component="Timeseries", props={},
        ))
        fileops = ReactBackend().render_tree(tree, config)
        self.assertEqual(len(fileops), 1)
        content = fileops[0].content

        self.assertIn("TimeseriesProps", content)
        self.assertIn("_props", content)
        self.assertIn("return", content)
        self.assertNotIn("__COMPOSITION__", content)

    def test_signature_override_with_extra_types(self):
        """extra_types from signature appear in generated output."""
        sig = self._make_signature("KpiRowProps", "data: KpiItem[];")
        sig["extra_types"] = ["interface KpiItem { label: string; value: number; }"]
        config = BackendConfig(
            output_base_path="src/",
            component_signatures={"KpiRow": sig},
        )
        tree = UIComponentTree(root=UIComponentNode(
            id="kr1", component="KpiRow", props={},
        ))
        fileops = ReactBackend().render_tree(tree, config)
        content = fileops[0].content

        self.assertIn("KpiItem", content)
        self.assertIn("_props", content)

    def test_signature_override_does_not_produce_empty_body(self):
        """Body should contain meaningful content even with signature."""
        sig = self._make_signature("MetricCardProps", "value?: number; label?: string;")
        config = BackendConfig(
            output_base_path="src/",
            component_signatures={"MetricCard": sig},
        )
        tree = UIComponentTree(root=UIComponentNode(
            id="mc1", component="MetricCard", props={},
        ))
        fileops = ReactBackend().render_tree(tree, config)
        content = fileops[0].content

        self.assertIn("export const MetricCard: React.FC<MetricCardProps>", content)
        self.assertIn("return", content)
        self.assertNotIn("__COMPOSITION__", content)

    def test_no_signature_fallback_never_uses_underscore_props(self):
        """Without signature override, hardcoded template uses destructured
        props (e.g. { metric }), not _props."""
        tree = UIComponentTree(root=UIComponentNode(
            id="ts1", component="Timeseries",
            props={"metric": "revenue"},
        ))
        config = BackendConfig(output_base_path="src/")
        fileops = ReactBackend().render_tree(tree, config)
        content = fileops[0].content

        self.assertNotIn("_props", content)
        self.assertIn("TimeseriesProps", content)
        self.assertIn("metric", content)


if __name__ == "__main__":
    unittest.main()
