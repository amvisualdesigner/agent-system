"""End-to-end pipeline validation tests with real catalog data.

Tests the integration points between:
  apply_example_context → build_component_tree → validate_symbol_graph

Uses actual ExampleContext + SkillContract data when possible.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.renderer.file_renderer import FileRenderer
from app.renderer.component_node import build_component_tree, ComponentNode
from app.renderer.symbol_graph import validate_symbol_graph
from app.examples.retrieval import retrieve_examples
from app.examples.catalog_loader import clear_cache
from app.examples.shaping import apply_example_context
from app.contracts.skill_registry import get_contract


def _tree_node_names(root: ComponentNode) -> set[str]:
    result: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        result.add(node.component)
        stack.extend(node.children)
    return result


class TestPipelineValidation(unittest.TestCase):
    """Scenario tests using real (or near-real) catalog and contract data."""

    def setUp(self):
        clear_cache()

    # ── Scenario 1: Phantom import real ──────────────────────────────────

    def test_phantom_import_revenue_chart(self):
        """Dashboard catalogo imports ./RevenueChart pero no hay file en config.

        Pipeline completo: retrieve → apply_example_context → build_component_tree
        → validate_symbol_graph. Debe REJECTED con phantom_import RevenueChart.
        """
        example_ctx = retrieve_examples("dashboard.sales_overview")
        contract = get_contract("dashboard.sales_overview", 1)

        ast = {
            "layout": "AnalyticsGrid",
            "nodes": [
                {"type": "KpiRow", "props": {"metrics": ["revenue", "growth"]}},
                {"type": "Timeseries", "props": {"metric": "revenue"}},
            ],
        }

        shaped = apply_example_context(ast, example_ctx)
        root = build_component_tree(shaped, contract.renderer, example_context=example_ctx)

        # Verify RevenueChart is a project import on the root
        root_imports = getattr(root, "imports", [])
        revenue_imports = [i for i in root_imports if "RevenueChart" in i]
        self.assertTrue(
            any("./RevenueChart" in i for i in revenue_imports),
            f"Precondition failed: no ./RevenueChart import on root. Imports: {root_imports}",
        )

        # RevenueChart is NOT in the tree
        node_names = _tree_node_names(root)
        self.assertNotIn(
            "RevenueChart", node_names,
            f"Precondition failed: RevenueChart found in tree: {node_names}",
        )

        ok, reason = validate_symbol_graph(root, contract.renderer)
        self.assertFalse(ok, "Expected REJECTED for phantom RevenueChart")
        self.assertIn("phantom_import", reason)
        self.assertIn("RevenueChart", reason)

    # ── Scenario 2: Self-import ──────────────────────────────────────────

    def test_self_import(self):
        """Node importa su propio nombre → self_import."""
        child = ComponentNode(
            component="KpiRow",
            file_path="src/pages/dashboard/components/KpiRow.tsx",
        )
        root = ComponentNode(
            component="SalesOverview",
            file_path="src/pages/dashboard/SalesOverview.tsx",
            imports=[
                "import { SalesOverview } from './SalesOverview'",
            ],
        )
        root.add_child(child)

        config = {
            "files": [
                {"path": "SalesOverview.tsx"},
                {"path": "components/KpiRow.tsx"},
            ],
        }

        ok, reason = validate_symbol_graph(root, config)
        self.assertFalse(ok)
        self.assertIn("self_import", reason)

    # ── Scenario 3: Valid graph ──────────────────────────────────────────

    def test_valid_graph(self):
        """Dashboard con KpiRow + Timeseries + layout, sin phantoms ni self.

        Se filtran los imports phantom del catalogo real para construir
        un grafo limpio.
        """
        example_ctx = retrieve_examples("dashboard.sales_overview")
        contract = get_contract("dashboard.sales_overview", 1)

        ast = {
            "layout": "AnalyticsGrid",
            "nodes": [
                {"type": "KpiRow", "props": {"metrics": ["revenue", "growth"]}},
                {"type": "Timeseries", "props": {"metric": "revenue"}},
            ],
        }

        shaped = apply_example_context(ast, example_ctx)
        root = build_component_tree(shaped, contract.renderer, example_context=example_ctx)

        phantom_paths = {"./RevenueChart", "./RevenueTable"}
        root.imports = [
            imp for imp in root.imports
            if not any(p in imp for p in phantom_paths)
        ]

        ok, reason = validate_symbol_graph(root, contract.renderer)
        self.assertTrue(ok, msg=f"Expected OK but got: {reason}")

    # ── Scenario 4: External import ignorado ─────────────────────────────

    def test_external_import_not_flagged(self):
        """import React from 'react' y @/ no son project imports → sin falsos positivos."""
        child = ComponentNode(
            component="KpiRow",
            file_path="src/pages/dashboard/components/KpiRow.tsx",
        )
        root = ComponentNode(
            component="SalesOverview",
            file_path="src/pages/dashboard/SalesOverview.tsx",
            imports=[
                "import React from 'react'",
                "import { useState } from 'react'",
                "import { Card } from '@/components/ui/Card'",
            ],
        )
        root.add_child(child)

        config = {
            "files": [
                {"path": "SalesOverview.tsx"},
                {"path": "components/KpiRow.tsx"},
            ],
        }

        ok, reason = validate_symbol_graph(root, config)
        self.assertTrue(ok, msg=f"Should pass but got: {reason}")

    # ── Scenario 5: Orphan composition ───────────────────────────────────

    def test_orphan_composition_from_catalog(self):
        """Composition target (RevenueChart/RevenueTable) sin nodo en el arbol.

        El catalogo dice SalesOverview compone RevenueChart, pero no hay
        archivo para RevenueChart en la config → orphan.
        """
        example_ctx = retrieve_examples("dashboard.sales_overview")
        contract = get_contract("dashboard.sales_overview", 1)

        # Verify precondition: composition targets exist
        comp = getattr(example_ctx, "composition", [])
        targets = {t for _, t in comp}
        self.assertIn("RevenueChart", targets,
                      msg="Precondition: RevenueChart must be in catalog composition")

        ast = {
            "layout": "AnalyticsGrid",
            "nodes": [
                {"type": "KpiRow", "props": {"metrics": ["revenue", "growth"]}},
                {"type": "Timeseries", "props": {"metric": "revenue"}},
            ],
        }

        shaped = apply_example_context(ast, example_ctx)
        root = build_component_tree(shaped, contract.renderer, example_context=example_ctx)

        node_names = _tree_node_names(root)
        self.assertNotIn("RevenueChart", node_names)
        self.assertIn("KpiRow", node_names)

        known_layouts = set(getattr(example_ctx, "layouts", []) or [])
        ok, reason = validate_symbol_graph(
            root, contract.renderer,
            composition=list(comp),
            known_layouts=known_layouts,
        )

        self.assertFalse(ok, "Expected REJECTED for orphan composition")
        self.assertIn("orphan_composition", reason)
        self.assertIn("RevenueChart", reason)

    def test_no_false_orphan_for_layouts(self):
        """DashboardLayout en composition NO se marca como orphan porque es layout conocido."""
        example_ctx = retrieve_examples("dashboard.sales_overview")
        comp = getattr(example_ctx, "composition", [])
        targets = {t for _, t in comp}
        self.assertIn("DashboardLayout", targets)

        ast = {
            "layout": "AnalyticsGrid",
            "nodes": [
                {"type": "KpiRow", "props": {"metrics": ["revenue", "growth"]}},
            ],
        }
        contract = get_contract("dashboard.sales_overview", 1)
        shaped = apply_example_context(ast, example_ctx)
        root = build_component_tree(shaped, contract.renderer, example_context=example_ctx)

        known_layouts = set(getattr(example_ctx, "layouts", []) or [])
        ok, reason = validate_symbol_graph(
            root, contract.renderer,
            composition=list(comp),
            known_layouts=known_layouts,
        )
        # Should fail for RevenueChart/RevenueTable, but NOT for DashboardLayout
        self.assertFalse(ok)
        self.assertNotIn("DashboardLayout", reason,
                         msg="DashboardLayout should not be flagged as orphan")


if __name__ == "__main__":
    unittest.main()
