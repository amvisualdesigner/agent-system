import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.examples.retrieval import retrieve_examples
from app.examples.catalog_loader import clear_cache, RENDERER_SCHEMA_VERSION


class TestRetrieval(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def test_dashboard_contract_returns_dashboard_context(self):
        ctx = retrieve_examples("dashboard.sales_overview")
        self.assertGreater(len(ctx.imports), 0)
        self.assertGreater(len(ctx.components), 0)
        self.assertIn("DashboardLayout", ctx.layouts)
        self.assertIn("SalesOverview", ctx.components)
        self.assertIn("KpiRow", ctx.components)

    def test_dashboard_imports_include_card(self):
        ctx = retrieve_examples("dashboard.sales_overview")
        card_imports = [i for i in ctx.imports if "Card" in i]
        self.assertGreater(len(card_imports), 0)

    def test_dashboard_imports_include_dashboard_layout(self):
        ctx = retrieve_examples("dashboard.sales_overview")
        layout_imports = [i for i in ctx.imports if "DashboardLayout" in i]
        self.assertGreater(len(layout_imports), 0)

    def test_dashboard_has_composition_edges(self):
        ctx = retrieve_examples("dashboard.sales_overview")
        self.assertGreater(len(ctx.composition), 0)
        sales_edges = [e for e in ctx.composition if e[0] == "SalesOverview"]
        self.assertGreater(len(sales_edges), 0)

    def test_table_contract_returns_tables_context(self):
        ctx = retrieve_examples("analytics.table")
        self.assertGreater(len(ctx.imports), 0)
        self.assertIn("AnalyticsTable", ctx.components)
        card_imports = [i for i in ctx.imports if "Card" in i]
        self.assertGreater(len(card_imports), 0)

    def test_unknown_contract_returns_empty_context(self):
        ctx = retrieve_examples("noop")
        self.assertEqual(len(ctx.imports), 0)
        self.assertEqual(len(ctx.components), 0)
        self.assertEqual(len(ctx.layouts), 0)
        self.assertEqual(len(ctx.composition), 0)

    def test_unknown_contract_id_returns_empty(self):
        ctx = retrieve_examples("nonexistent.contract")
        self.assertEqual(len(ctx.imports), 0)

    def test_context_empty_factory(self):
        from app.examples.models import ExampleContext
        ctx = ExampleContext.empty()
        self.assertEqual(len(ctx.imports), 0)
        self.assertEqual(len(ctx.components), 0)
        self.assertEqual(len(ctx.layouts), 0)
        self.assertEqual(len(ctx.composition), 0)

    def test_format_imports_block(self):
        ctx = retrieve_examples("dashboard.sales_overview")
        block = ctx.format_imports_block()
        self.assertIn("import React from 'react'", block)
        self.assertIn("Card", block)

    def test_main_layout(self):
        ctx = retrieve_examples("dashboard.sales_overview")
        self.assertEqual(ctx.main_layout(), "DashboardLayout")

    def test_empty_main_layout(self):
        from app.examples.models import ExampleContext
        ctx = ExampleContext.empty()
        self.assertEqual(ctx.main_layout(), "")

    def test_renderer_schema_version_constant(self):
        self.assertEqual(RENDERER_SCHEMA_VERSION, "2026-05")


if __name__ == "__main__":
    unittest.main()
