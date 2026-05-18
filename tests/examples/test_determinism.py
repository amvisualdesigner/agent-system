import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.renderer.file_renderer import FileRenderer
from app.renderer.compiler import CompilerConfig
from app.examples.models import ExampleContext
from app.examples.catalog_loader import clear_cache
from app.examples.retrieval import retrieve_examples


class TestDeterminism(unittest.TestCase):
    def setUp(self):
        clear_cache()
        self.renderer = FileRenderer()
        self.compiler_config = CompilerConfig(mode="legacy")

    def test_same_ast_same_render_no_context(self):
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue", "growth"]}}]}
        config = {
            "base_path": "src/",
            "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}],
        }
        r1 = self.renderer.render(ast, config, compiler_config=self.compiler_config)
        r2 = self.renderer.render(ast, config, compiler_config=self.compiler_config)
        self.assertEqual(r1[0].content, r2[0].content)

    def test_same_ast_same_render_with_context(self):
        ctx = ExampleContext(
            imports=["import { Card } from '@/components/ui/Card'"],
            components=["KpiRow"],
            layouts=[],
            composition=[],
        )
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config = {
            "base_path": "src/",
            "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}],
        }
        r1 = self.renderer.render(ast, config, example_context=ctx, compiler_config=self.compiler_config)
        r2 = self.renderer.render(ast, config, example_context=ctx, compiler_config=self.compiler_config)
        self.assertEqual(r1[0].content, r2[0].content)

    def test_same_contract_same_catalog_every_time(self):
        c1 = retrieve_examples("dashboard.sales_overview")
        clear_cache()
        c2 = retrieve_examples("dashboard.sales_overview")
        self.assertEqual(c1.imports, c2.imports)
        self.assertEqual(c1.components, c2.components)
        self.assertEqual(c1.layouts, c2.layouts)
        self.assertEqual(c1.composition, c2.composition)

    def test_render_deterministic_across_calls(self):
        from app.examples.retrieval import retrieve_examples
        ast = {
            "nodes": [
                {"type": "SalesOverview", "props": {"metrics": ["revenue"], "timeseries_metric": "revenue"}},
            ],
        }
        config = {
            "base_path": "src/pages/dashboard/",
            "files": [{"path": "SalesOverview.tsx", "template": "dashboard_page.j2"}],
        }
        ctx = retrieve_examples("dashboard.sales_overview")
        r1 = self.renderer.render(ast, config, example_context=ctx, compiler_config=self.compiler_config)
        r2 = self.renderer.render(ast, config, example_context=ctx, compiler_config=self.compiler_config)
        self.assertEqual(r1[0].content, r2[0].content)

    def test_fileops_structure_is_stable(self):
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config = {
            "base_path": "src/",
            "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}],
        }
        fileops = self.renderer.render(ast, config, compiler_config=self.compiler_config)
        fop = fileops[0]
        self.assertEqual(fop.action, "create")
        self.assertTrue(fop.path.endswith(".tsx"))
        self.assertTrue(fop.content.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
