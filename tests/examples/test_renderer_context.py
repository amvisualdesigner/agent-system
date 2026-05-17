import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.renderer.file_renderer import FileRenderer
from app.examples.models import ExampleContext


class TestRendererContext(unittest.TestCase):
    def setUp(self):
        self.renderer = FileRenderer()

    def test_render_without_context_produces_valid_output(self):
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config = {
            "base_path": "src/",
            "files": [
                {"path": "Test.tsx", "template": "kpi_row.j2"},
            ],
        }
        fileops = self.renderer.render(ast, config)
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
        fileops = self.renderer.render(ast, config, example_context=ctx)
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
        fileops = self.renderer.render(ast, config, example_context=ctx)
        self.assertEqual(len(fileops), 3)

        overview = [f for f in fileops if "SalesOverview" in f.path][0].content
        self.assertIn("DashboardLayout", overview)
        self.assertIn("Card", overview)
        self.assertIn("KpiRow", overview)
        self.assertIn("Timeseries", overview)

    def test_render_with_context_includes_canonical_imports(self):
        from app.examples.retrieval import retrieve_examples
        ctx = retrieve_examples("dashboard.sales_overview")
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config = {
            "base_path": "src/",
            "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}],
        }
        fileops = self.renderer.render(ast, config, example_context=ctx)
        content = fileops[0].content
        self.assertIn("import { Card } from '@/components/ui/Card'", content)


if __name__ == "__main__":
    unittest.main()
