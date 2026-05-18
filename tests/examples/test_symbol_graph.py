import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.renderer.component_node import ComponentNode
from app.renderer.symbol_graph import (
    _is_project_import,
    _collect_nodes,
    validate_symbol_graph,
)


class TestIsProjectImport(unittest.TestCase):
    def test_relative_dot_slash(self):
        self.assertTrue(_is_project_import("import { X } from './foo'"))

    def test_relative_dot_dot(self):
        self.assertTrue(_is_project_import("import { X } from '../bar'"))

    def test_alias_path(self):
        self.assertFalse(_is_project_import("import { X } from '@/components/Card'"))

    def test_external_dep(self):
        self.assertFalse(_is_project_import("import React from 'react'"))

    def test_external_scoped(self):
        self.assertFalse(_is_project_import("import { X } from '@scope/pkg'"))

    def test_no_from_clause(self):
        self.assertFalse(_is_project_import("// just a comment"))


class TestCollectNodes(unittest.TestCase):
    def test_collects_root(self):
        root = ComponentNode(component="Dashboard", file_path="Dashboard.tsx")
        nodes = _collect_nodes(root)
        self.assertIn("Dashboard", nodes)

    def test_collects_children(self):
        root = ComponentNode(component="Dashboard", file_path="Dashboard.tsx")
        child = ComponentNode(component="KpiRow", file_path="KpiRow.tsx")
        root.add_child(child)
        nodes = _collect_nodes(root)
        self.assertIn("Dashboard", nodes)
        self.assertIn("KpiRow", nodes)
        self.assertEqual(len(nodes), 2)

    def test_collects_grandchildren(self):
        root = ComponentNode(component="Page", file_path="Page.tsx")
        section = ComponentNode(component="Section", file_path="Section.tsx")
        widget = ComponentNode(component="Widget", file_path="Widget.tsx")
        root.add_child(section)
        section.add_child(widget)
        nodes = _collect_nodes(root)
        self.assertIn("Page", nodes)
        self.assertIn("Section", nodes)
        self.assertIn("Widget", nodes)

    def test_no_mutation(self):
        root = ComponentNode(component="Dashboard", file_path="Dashboard.tsx")
        child = ComponentNode(component="KpiRow", file_path="KpiRow.tsx")
        root.add_child(child)
        _collect_nodes(root)
        self.assertEqual(len(root.children), 1)


class TestValidateSymbolGraph(unittest.TestCase):
    def test_valid_graph_passes(self):
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
            imports=[
                "import React from 'react'",
                "import { Card } from '@/components/ui/Card'",
            ],
        )
        root.add_child(child)
        config = {
            "files": [
                {"path": "Dashboard.tsx"},
                {"path": "components/KpiRow.tsx"},
            ],
        }
        ok, reason = validate_symbol_graph(root, config)
        self.assertTrue(ok, msg=reason)

    def test_phantom_import_detected(self):
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
            imports=[
                "import { RevenueChart } from './RevenueChart'",
            ],
        )
        root.add_child(child)
        config = {
            "files": [
                {"path": "Dashboard.tsx"},
                {"path": "components/KpiRow.tsx"},
            ],
        }
        ok, reason = validate_symbol_graph(root, config)
        self.assertFalse(ok)
        self.assertIn("phantom_import", reason)
        self.assertIn("RevenueChart", reason)

    def test_self_import_detected(self):
        root = ComponentNode(
            component="Dashboard",
            file_path="src/Dashboard.tsx",
            props={},
            template="dashboard_page.j2",
            imports=[
                "import { Dashboard } from './Dashboard'",
            ],
        )
        config = {
            "files": [
                {"path": "Dashboard.tsx"},
            ],
        }
        ok, reason = validate_symbol_graph(root, config)
        self.assertFalse(ok)
        self.assertIn("self_import", reason)

    def test_external_import_not_flagged(self):
        root = ComponentNode(
            component="Dashboard",
            file_path="src/Dashboard.tsx",
            props={},
            template="dashboard_page.j2",
            imports=[
                "import React from 'react'",
                "import { useState } from 'react'",
            ],
        )
        config = {
            "files": [
                {"path": "Dashboard.tsx"},
            ],
        }
        ok, reason = validate_symbol_graph(root, config)
        self.assertTrue(ok, msg=reason)

    def test_empty_tree_passes(self):
        root = ComponentNode(
            component="Dashboard",
            file_path="src/Dashboard.tsx",
            props={},
        )
        config = {
            "files": [
                {"path": "Dashboard.tsx"},
            ],
        }
        ok, reason = validate_symbol_graph(root, config)
        self.assertTrue(ok, msg=reason)

    def test_multiple_issues_reported(self):
        child = ComponentNode(
            component="KpiRow",
            file_path="src/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        root = ComponentNode(
            component="Dashboard",
            file_path="src/Dashboard.tsx",
            props={},
            template="dashboard_page.j2",
            imports=[
                "import { Dashboard } from './Dashboard'",
                "import { PhantomWidget } from './PhantomWidget'",
            ],
        )
        root.add_child(child)
        config = {
            "files": [
                {"path": "Dashboard.tsx"},
                {"path": "KpiRow.tsx"},
            ],
        }
        ok, reason = validate_symbol_graph(root, config)
        self.assertFalse(ok)
        self.assertIn("self_import", reason)
        self.assertIn("phantom_import", reason)
        self.assertIn("PhantomWidget", reason)


if __name__ == "__main__":
    unittest.main()
