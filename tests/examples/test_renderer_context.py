import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.renderer.file_renderer import FileRenderer
from app.renderer.compiler import CompilerConfig
from app.examples.models import ExampleContext


class TestRendererContext(unittest.TestCase):
    def setUp(self):
        self.renderer = FileRenderer()
        self.compiler_config = CompilerConfig(mode="legacy")

    def test_render_without_context_produces_valid_output(self):
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config = {
            "base_path": "src/",
            "files": [
                {"path": "Test.tsx", "template": "kpi_row.j2"},
            ],
        }
        fileops = self.renderer.render(ast, config, compiler_config=self.compiler_config)
        self.assertEqual(len(fileops), 1)
        content = fileops[0].content
        self.assertIn("KpiRow", content)
        self.assertIn("revenue", content)

    def test_render_with_context_still_produces_valid_output(self):
        ctx = ExampleContext(
            imports=["import { Card } from '@/components/ui/Card'"],
            components=["KpiRow"],
            layouts=[],
            composition=[],
        )
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config = {
            "base_path": "src/",
            "files": [
                {"path": "Test.tsx", "template": "kpi_row.j2"},
            ],
        }
        fileops = self.renderer.render(ast, config, example_context=ctx, compiler_config=self.compiler_config)
        self.assertEqual(len(fileops), 1)
        content = fileops[0].content
        self.assertIn("KpiRow", content)
        self.assertIn("revenue", content)

    def test_render_with_dashboard_context(self):
        from app.examples.retrieval import retrieve_examples
        ctx = retrieve_examples("dashboard.sales_overview")
        ast = {
            "nodes": [
                {"type": "SalesOverview", "props": {"metrics": ["revenue", "growth"], "timeseries_metric": "revenue"}},
            ],
        }
        config = {
            "base_path": "src/pages/dashboard/",
            "files": [
                {"path": "SalesOverview.tsx", "template": "dashboard_page.j2"},
                {"path": "components/KpiRow.tsx", "template": "kpi_row.j2"},
                {"path": "components/Timeseries.tsx", "template": "timeseries.j2"},
            ],
        }
        fileops = self.renderer.render(ast, config, example_context=ctx, compiler_config=self.compiler_config)
        self.assertEqual(len(fileops), 3)

        overview = [f for f in fileops if "SalesOverview" in f.path][0].content
        self.assertIn("DashboardLayout", overview)
        self.assertIn("Card", overview)
        self.assertIn("KpiRow", overview)
        self.assertIn("Timeseries", overview)

    def test_render_context_example_imports_in_output(self):
        ctx = ExampleContext(
            imports=["import { SpecialWidget } from './SpecialWidget'"],
            components=["KpiRow"],
            layouts=[],
            composition=[],
        )
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config = {
            "base_path": "src/",
            "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}],
        }
        fileops = self.renderer.render(ast, config, example_context=ctx, compiler_config=self.compiler_config)
        content = fileops[0].content
        self.assertIn("SpecialWidget", content)

    def test_render_context_layout_hints_in_output(self):
        ctx = ExampleContext(
            imports=[],
            components=[],
            layouts=["CustomLayout"],
            composition=[],
        )
        ast = {
            "nodes": [
                {"type": "SalesOverview", "props": {"metrics": ["revenue"], "timeseries_metric": "revenue"}},
            ],
        }
        config = {
            "base_path": "src/",
            "files": [{"path": "SalesOverview.tsx", "template": "dashboard_page.j2"}],
        }
        fileops = self.renderer.render(ast, config, example_context=ctx, compiler_config=self.compiler_config)
        content = fileops[0].content
        self.assertIn("<CustomLayout>", content)
        self.assertIn("</CustomLayout>", content)


if __name__ == "__main__":
    unittest.main()
