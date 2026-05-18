"""Phase 4 tests: file-scoped emission, enrichment pass, single-tree pipeline."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.renderer.component_node import (
    ComponentNode,
    emit_file,
    emit_tree,
    render_node,
    resolve_imports,
    build_component_tree,
    _resolve_imports,
)
from app.renderer.symbol_graph import validate_symbol_graph
from app.renderer.validators import validate_fileops
from app.renderer.base import FileOp
from app.examples.shaping import apply_example_context
from app.contracts.skill_registry import get_contract
from app.examples.retrieval import retrieve_examples
from app.examples.catalog_loader import clear_cache


class TestEmitFile(unittest.TestCase):
    """emit_file: core Phase 4 primitive."""

    def test_emit_file_leaf_node(self):
        node = ComponentNode(
            component="KpiRow",
            file_path="src/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            imports=["import React from 'react'"],
            template="kpi_row.j2",
        )
        resolve_imports(node)
        op = emit_file(node)
        self.assertIsNotNone(op)
        self.assertEqual(op.action, "create")
        self.assertEqual(op.path, "src/KpiRow.tsx")
        self.assertIn("KpiRow", op.content)
        self.assertIn("revenue", op.content)

    def test_emit_file_no_template_returns_none(self):
        node = ComponentNode(
            component="Page",
            file_path="src/Page.tsx",
            props={},
        )
        op = emit_file(node)
        self.assertIsNone(op)

    def test_emit_file_deterministic(self):
        def build():
            return ComponentNode(
                component="KpiRow",
                file_path="src/KpiRow.tsx",
                props={"metrics": ["revenue"]},
                template="kpi_row.j2",
            )
        n1, n2 = build(), build()
        resolve_imports(n1)
        resolve_imports(n2)
        r1 = emit_file(n1)
        r2 = emit_file(n2)
        self.assertEqual(r1.content, r2.content)


class TestResolveImports(unittest.TestCase):
    """resolve_imports enrichment pass: pre-computes node.resolved_imports."""

    def test_sets_resolved_imports_on_leaf(self):
        node = ComponentNode(
            component="KpiRow",
            file_path="src/KpiRow.tsx",
            imports=["import React from 'react'"],
            template="kpi_row.j2",
        )
        resolve_imports(node)
        self.assertIsNotNone(node.resolved_imports)
        self.assertIn("React", node.resolved_imports)

    def test_sets_resolved_imports_on_root_with_children(self):
        child = ComponentNode(
            component="KpiRow",
            file_path="src/components/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        root = ComponentNode(
            component="Dashboard",
            file_path="src/Dashboard.tsx",
            props={},
            template="dashboard_page.j2",
            layout="DashboardLayout",
        )
        root.add_child(child)
        resolve_imports(root)
        self.assertIsNotNone(root.resolved_imports)
        self.assertIn("KpiRow", root.resolved_imports)

    def test_leaf_without_imports_gets_empty_string(self):
        node = ComponentNode(
            component="KpiRow",
            file_path="src/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        resolve_imports(node)
        self.assertEqual(node.resolved_imports, "")
        self.assertIsNotNone(node.resolved_imports)

    def test_node_without_template_gets_imports(self):
        node = ComponentNode(
            component="Dashboard",
            file_path="src/Dashboard.tsx",
            imports=["import { Card } from '@/components/ui/Card'"],
        )
        resolve_imports(node)
        self.assertIn("Card", node.resolved_imports)

    def test_dedup_against_template(self):
        """kpi_row.j2 has 'import React from 'react';' and Card hardcoded.
        resolve_imports should dedup them. Imports NOT in template survive."""
        node = ComponentNode(
            component="KpiRow",
            file_path="src/KpiRow.tsx",
            imports=[
                "import React from 'react';",
                "import { KpiValue } from './KpiValue';",
            ],
            template="kpi_row.j2",
        )
        resolve_imports(node)
        self.assertNotIn(
            "import React from 'react';", node.resolved_imports,
            "React import is hardcoded in template, should be deduped",
        )
        self.assertIn("KpiValue", node.resolved_imports,
                      "KpiValue is NOT in template, should survive dedup")

    def test_no_mutation_of_node_imports(self):
        original_imports = ["import React from 'react'"]
        node = ComponentNode(
            component="KpiRow",
            file_path="src/KpiRow.tsx",
            imports=list(original_imports),
            template="kpi_row.j2",
        )
        resolve_imports(node)
        self.assertEqual(node.imports, original_imports)


class TestEmitFileWithEnrichment(unittest.TestCase):
    """Full enrichment + emission path."""

    def test_enrich_then_emit_root_with_child(self):
        child = ComponentNode(
            component="KpiRow",
            file_path="src/components/KpiRow.tsx",
            props={"metrics": ["revenue", "growth"]},
            template="kpi_row.j2",
        )
        root = ComponentNode(
            component="Dashboard",
            file_path="src/Dashboard.tsx",
            props={},
            template="dashboard_page.j2",
            layout="DashboardLayout",
        )
        root.add_child(child)

        resolve_imports(root)
        fileops = emit_tree(root)

        self.assertEqual(len(fileops), 2)
        root_op = [f for f in fileops if "Dashboard.tsx" in f.path][0]
        child_op = [f for f in fileops if "KpiRow.tsx" in f.path][0]

        self.assertIn("KpiRow", root_op.content)
        self.assertIn("revenue", child_op.content)
        self.assertIn("growth", child_op.content)

    def test_enrich_then_emit_no_template_node(self):
        child = ComponentNode(
            component="KpiRow",
            file_path="src/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        root = ComponentNode(
            component="Page",
            file_path="src/Page.tsx",
            props={},
        )
        root.add_child(child)

        resolve_imports(root)
        fileops = emit_tree(root)

        # Root has no template → skipped
        # Child has template → emitted
        self.assertEqual(len(fileops), 1)
        self.assertEqual(fileops[0].path, "src/KpiRow.tsx")


class TestPipelinePhase4(unittest.TestCase):
    """End-to-end Phase 4 pipeline with real catalog data."""

    def setUp(self):
        clear_cache()

    def test_single_tree_pipeline_with_real_catalog(self):
        """Full pre-processing → build_component_tree → resolve_imports
        → emit_tree → SymbolGraph → validate_fileops."""
        from app.engine.plan_normalizer import normalize_plan
        from app.renderer.symbol_graph import _is_project_import
        from app.renderer.component_node import (
            _extract_component_name,
            _extract_imported_name,
        )
        from dataclasses import replace

        example_ctx = retrieve_examples("dashboard.sales_overview")
        contract = get_contract("dashboard.sales_overview", 1)

        # Build minimal AST (no semantic intents needed for this contract)
        ast = {
            "layout": "AnalyticsGrid",
            "nodes": [
                {"type": "KpiRow", "props": {"metrics": ["revenue", "growth"]}},
                {"type": "Timeseries", "props": {"metric": "revenue"}},
            ],
        }

        # --- Pre-processing (Phase 3) ---
        files = contract.renderer.get("files", [])
        component_registry = {_extract_component_name(f["path"]) for f in files}
        normalized = normalize_plan(
            {"actions": ast.get("nodes", [])}, component_registry,
        )
        normalized_ast = dict(ast)
        normalized_ast["nodes"] = normalized["actions"]

        normalized_node_names = {
            n.get("type") for n in normalized_ast.get("nodes", [])
        }
        filtered_imports = list(example_ctx.imports)
        for i, imp in enumerate(filtered_imports):
            if not _is_project_import(imp):
                continue
            name = _extract_imported_name(imp)
            if name and name not in normalized_node_names and name not in component_registry:
                filtered_imports[i] = None
        filtered_imports = [i for i in filtered_imports if i is not None]

        known_layouts = set(getattr(example_ctx, "layouts", []) or [])
        filtered_composition = [
            (p, c) for p, c in (getattr(example_ctx, "composition", []) or [])
            if c in normalized_node_names or c in known_layouts
        ]
        normalized_ctx = replace(
            example_ctx,
            imports=filtered_imports,
            composition=filtered_composition,
        )

        shaped = apply_example_context(normalized_ast, normalized_ctx)

        # --- Phase 4 pipeline ---
        root = build_component_tree(shaped, contract.renderer, example_context=normalized_ctx)

        # Enrichment pass
        resolve_imports(root)
        self.assertIsNotNone(root.resolved_imports)
        for child in root.children:
            self.assertIsNotNone(child.resolved_imports,
                                 f"Child {child.component} should have resolved_imports")

        # Emission
        fileops = emit_tree(root)
        self.assertGreater(len(fileops), 0)
        for fop in fileops:
            self.assertIsInstance(fop, FileOp)
            self.assertEqual(fop.action, "create")

        # Post-check validation
        ok, sg_reason = validate_symbol_graph(
            root, contract.renderer,
            composition=getattr(normalized_ctx, "composition", None),
            known_layouts=known_layouts,
        )
        self.assertTrue(ok, msg=sg_reason)

        ok, vreason = validate_fileops(fileops)
        self.assertTrue(ok, msg=vreason)


if __name__ == "__main__":
    unittest.main()
