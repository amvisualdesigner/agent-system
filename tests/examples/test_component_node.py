import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.renderer.component_node import (
    ComponentNode,
    build_component_tree,
    render_node,
    emit_tree,
    resolve_imports,
    render_tree_string,
    _build_node_context,
    _extract_component_name,
    _extract_imports,
    _extract_layout,
    _extract_imported_name,
    _resolve_imports,
)
from app.renderer.base import FileOp
from app.examples.models import ExampleContext


class TestComponentNode(unittest.TestCase):
    """ComponentNode dataclass and helpers."""

    def test_node_creation(self):
        node = ComponentNode(
            component="KpiRow",
            file_path="src/components/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            imports=["import { Card } from '@/components/ui/Card'"],
        )
        self.assertEqual(node.component, "KpiRow")
        self.assertEqual(node.props["metrics"], ["revenue"])
        self.assertEqual(len(node.imports), 1)
        self.assertEqual(node.parent, None)
        self.assertEqual(node.children, [])

    def test_add_child_sets_parent(self):
        parent = ComponentNode(component="Page", file_path="Page.tsx")
        child = ComponentNode(component="Widget", file_path="Widget.tsx")
        parent.add_child(child)
        self.assertEqual(len(parent.children), 1)
        self.assertEqual(child.parent, parent)


class TestExtractComponentName(unittest.TestCase):
    def test_simple_name(self):
        self.assertEqual(_extract_component_name("KpiRow.tsx"), "KpiRow")

    def test_subdirectory(self):
        self.assertEqual(
            _extract_component_name("components/KpiRow.tsx"), "KpiRow"
        )

    def test_deep_path(self):
        self.assertEqual(
            _extract_component_name("src/pages/dashboard/SalesOverview.tsx"),
            "SalesOverview",
        )

    def test_dotted_name(self):
        self.assertEqual(
            _extract_component_name("some.dir/MyComponent.test.tsx"),
            "MyComponent.test",
        )


class TestExtractImports(unittest.TestCase):
    def test_from_ast(self):
        ast = {"__example_imports__": ["import A", "import B"]}
        result = _extract_imports(ast, None)
        self.assertEqual(result, ["import A", "import B"])

    def test_from_example_context(self):
        ast = {}
        ctx = ExampleContext(imports=["import X", "import Y"])
        result = _extract_imports(ast, ctx)
        self.assertEqual(result, ["import X", "import Y"])

    def test_example_context_is_primary_source(self):
        ast = {"__example_imports__": ["import A"]}
        ctx = ExampleContext(imports=["import B"])
        result = _extract_imports(ast, ctx)
        self.assertEqual(result, ["import B"])

    def test_neither(self):
        result = _extract_imports({}, None)
        self.assertEqual(result, [])


class TestExtractLayout(unittest.TestCase):
    def test_from_ast_layout_key(self):
        ast = {"layout": "Grid"}
        result = _extract_layout(ast, None)
        self.assertEqual(result, "Grid")

    def test_from_ast_dunder_layout(self):
        ast = {"__layout__": "DashboardLayout"}
        result = _extract_layout(ast, None)
        self.assertEqual(result, "DashboardLayout")

    def test_from_example_context(self):
        ast = {}
        ctx = ExampleContext(layouts=["CustomLayout"])
        result = _extract_layout(ast, ctx)
        self.assertEqual(result, "CustomLayout")

    def test_ast_takes_priority(self):
        ast = {"__layout__": "FromAst"}
        ctx = ExampleContext(layouts=["FromCtx"])
        result = _extract_layout(ast, ctx)
        self.assertEqual(result, "FromAst")

    def test_none(self):
        result = _extract_layout({}, None)
        self.assertIsNone(result)


class TestBuildComponentTree(unittest.TestCase):
    """Tree builder: flat AST dict + renderer_config -> ComponentNode tree."""

    def test_single_file_single_slot(self):
        ast = {
            "nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}],
        }
        config = {
            "base_path": "src/",
            "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}],
        }
        root = build_component_tree(ast, config)
        self.assertEqual(root.component, "KpiRow")
        self.assertEqual(root.props, {"metrics": ["revenue"]})
        self.assertEqual(len(root.children), 0)

    def test_single_file_name_mismatch_fallback(self):
        ast = {
            "nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}],
        }
        config = {
            "base_path": "src/",
            "files": [{"path": "Test.tsx", "template": "kpi_row.j2"}],
        }
        root = build_component_tree(ast, config)
        # Single-slot fallback: root gets the only slot's props
        self.assertEqual(root.component, "Test")
        self.assertEqual(root.props, {"metrics": ["revenue"]})

    def test_root_composition_children_with_slots(self):
        ast = {
            "layout": "AnalyticsGrid",
            "nodes": [
                {"type": "KpiRow", "props": {"metrics": ["revenue", "growth"]}},
                {"type": "Timeseries", "props": {"metric": "revenue"}},
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
        root = build_component_tree(ast, config)

        # Root is composition node with no props
        self.assertEqual(root.component, "SalesOverview")
        self.assertEqual(root.props, {})
        self.assertEqual(root.layout, "AnalyticsGrid")

        # Children matched by name with their own props
        self.assertEqual(len(root.children), 2)
        self.assertEqual(root.children[0].component, "KpiRow")
        self.assertEqual(root.children[0].props, {"metrics": ["revenue", "growth"]})
        self.assertEqual(root.children[1].component, "Timeseries")
        self.assertEqual(root.children[1].props, {"metric": "revenue"})

        # Parent links
        self.assertIs(root.children[0].parent, root)
        self.assertIs(root.children[1].parent, root)

    def test_imports_from_example_context(self):
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config = {
            "base_path": "src/",
            "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}],
        }
        ctx = ExampleContext(imports=["import { Widget } from './Widget'"])
        root = build_component_tree(ast, config, example_context=ctx)
        self.assertIn("import { Widget } from './Widget'", root.imports)

    def test_layout_from_example_context(self):
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config = {
            "base_path": "src/",
            "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}],
        }
        ctx = ExampleContext(layouts=["CustomLayout"])
        root = build_component_tree(ast, config, example_context=ctx)
        self.assertEqual(root.layout, "CustomLayout")

    def test_composition_ordering_from_example_context(self):
        ast = {
            "nodes": [
                {"type": "Timeseries", "props": {"metric": "revenue"}},
                {"type": "KpiRow", "props": {"metrics": ["revenue", "growth"]}},
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
        ctx = ExampleContext(
            composition=[("SalesOverview", "KpiRow"), ("SalesOverview", "Timeseries")],
        )
        root = build_component_tree(ast, config, example_context=ctx)

        # Children are reordered to match composition hints
        self.assertEqual(root.children[0].component, "KpiRow")
        self.assertEqual(root.children[1].component, "Timeseries")

    def test_composition_ordering_without_context_preserves_file_order(self):
        ast = {
            "nodes": [
                {"type": "Timeseries", "props": {"metric": "revenue"}},
                {"type": "KpiRow", "props": {"metrics": ["revenue", "growth"]}},
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
        root = build_component_tree(ast, config)

        # Without composition hints, children are ordered by file iteration
        self.assertEqual(root.children[0].component, "KpiRow")
        self.assertEqual(root.children[1].component, "Timeseries")


class TestBuildNodeContext(unittest.TestCase):
    """Scoped context: each node sees ONLY its own props."""

    def test_leaf_node_props_only(self):
        node = ComponentNode(
            component="KpiRow",
            file_path="KpiRow.tsx",
            props={"metrics": ["revenue", "growth"], "metric": "revenue"},
        )
        ctx = _build_node_context(node)
        self.assertEqual(ctx["metrics"], ["revenue", "growth"])
        self.assertEqual(ctx["metric"], "revenue")

    def test_composition_root_no_props(self):
        node = ComponentNode(
            component="SalesOverview",
            file_path="SalesOverview.tsx",
            props={},
            layout="DashboardLayout",
        )
        ctx = _build_node_context(node)
        self.assertNotIn("metrics", ctx)
        self.assertIn("LAYOUT_OPEN", ctx)
        self.assertIn("LAYOUT_CLOSE", ctx)

    def test_no_global_context_mutation(self):
        node_a = ComponentNode(
            component="KpiRow",
            file_path="KpiRow.tsx",
            props={"metrics": ["revenue"]},
        )
        node_b = ComponentNode(
            component="Timeseries",
            file_path="Timeseries.tsx",
            props={"metric": "growth"},
        )
        ctx_a = _build_node_context(node_a)
        ctx_b = _build_node_context(node_b)

        self.assertIn("metrics", ctx_a)
        self.assertNotIn("metric", ctx_a)
        self.assertIn("metric", ctx_b)
        self.assertNotIn("metrics", ctx_b)

    def test_imports_in_context(self):
        node = ComponentNode(
            component="KpiRow",
            file_path="KpiRow.tsx",
            props={"metrics": ["revenue"]},
            imports=["import { Card } from 'ui'"],
        )
        ctx = _build_node_context(node)
        self.assertIn("EXAMPLE_IMPORTS", ctx)
        self.assertIn("Card", ctx["EXAMPLE_IMPORTS"])


class TestImportResolution(unittest.TestCase):
    """Import resolution: merge + dedup from catalog and tree."""

    def test_extract_imported_name_default(self):
        self.assertEqual(_extract_imported_name("import React from 'react'"), "React")

    def test_extract_imported_name_named(self):
        self.assertEqual(
            _extract_imported_name("import { Card } from '@/components/ui/Card'"),
            "Card",
        )

    def test_extract_imported_name_none(self):
        self.assertIsNone(_extract_imported_name("// just a comment"))

    def test_resolve_merges_catalog_and_tree_imports(self):
        root = ComponentNode(
            component="Dashboard",
            file_path="src/Dashboard.tsx",
            props={},
            imports=[
                "import React from 'react'",
                "import { Card } from '@/components/ui/Card'",
            ],
        )
        child = ComponentNode(
            component="KpiRow",
            file_path="src/components/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        root.add_child(child)

        resolved = _resolve_imports(root)
        sources = {_extract_imported_name(l): l for l in resolved}

        # Catalog imports preserved
        self.assertIn("React", sources)
        self.assertIn("Card", sources)

        # Tree-derived child import present
        self.assertIn("KpiRow", sources)
        self.assertIn("./components/KpiRow", sources["KpiRow"])

    def test_resolve_tree_overrides_catalog_for_same_component(self):
        root = ComponentNode(
            component="Dashboard",
            file_path="src/Dashboard.tsx",
            props={},
            imports=[
                "import { KpiRow } from './KpiRow'",
            ],
        )
        child = ComponentNode(
            component="KpiRow",
            file_path="src/components/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        root.add_child(child)

        resolved = _resolve_imports(root)
        kpi_line = next(l for l in resolved if "KpiRow" in l)

        # Tree-derived path wins over catalog's incorrect path
        self.assertIn("./components/KpiRow", kpi_line)
        self.assertNotIn("'./KpiRow'", kpi_line)

    def test_resolve_no_duplicates(self):
        root = ComponentNode(
            component="Dashboard",
            file_path="src/Dashboard.tsx",
            props={},
            imports=[
                "import React from 'react'",
                "import { Card } from '@/components/ui/Card'",
                "import { KpiRow } from './KpiRow'",
            ],
        )
        child = ComponentNode(
            component="KpiRow",
            file_path="src/components/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        root.add_child(child)

        resolved = _resolve_imports(root)
        kpi_lines = [l for l in resolved if "KpiRow" in l]
        self.assertEqual(len(kpi_lines), 1)

    def test_resolve_returns_empty_for_leaf_without_imports(self):
        leaf = ComponentNode(
            component="KpiRow",
            file_path="src/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        resolved = _resolve_imports(leaf)
        self.assertEqual(resolved, [])


class TestRenderNode(unittest.TestCase):
    """Single-node emission: renders one ComponentNode into a FileOp."""

    def test_render_leaf_node(self):
        node = ComponentNode(
            component="KpiRow",
            file_path="src/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            imports=["import React from 'react'"],
            template="kpi_row.j2",
        )
        resolve_imports(node)
        op = render_node(node)
        self.assertIsNotNone(op)
        self.assertEqual(op.action, "create")
        self.assertEqual(op.path, "src/KpiRow.tsx")
        self.assertIn("KpiRow", op.content)
        self.assertIn("revenue", op.content)

    def test_render_composition_node_returns_none(self):
        node = ComponentNode(
            component="Page",
            file_path="src/Page.tsx",
            props={},
        )
        op = render_node(node)
        self.assertIsNone(op)

    def test_analytics_table_isolation(self):
        node = ComponentNode(
            component="AnalyticsTable",
            file_path="src/AnalyticsTable.tsx",
            props={
                "columns": ["Name", "Value"],
                "table_data": [["Foo", 42]],
            },
            template="analytics_table.j2",
        )
        resolve_imports(node)
        op = render_node(node)
        self.assertIsNotNone(op)
        content = op.content
        self.assertIn("<th>Name</th>", content)
        self.assertIn("<th>Value</th>", content)
        self.assertIn("Foo", content)
        self.assertIn("42", content)

    def test_render_single_node_deterministic(self):
        def build():
            node = ComponentNode(
                component="KpiRow",
                file_path="src/KpiRow.tsx",
                props={"metrics": ["revenue"]},
                template="kpi_row.j2",
            )
            resolve_imports(node)
            return node

        r1 = render_node(build())
        r2 = render_node(build())
        self.assertEqual(r1.content, r2.content)

    def test_render_fileop_structure_stable(self):
        node = ComponentNode(
            component="KpiRow",
            file_path="src/Test.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        resolve_imports(node)
        op = render_node(node)
        self.assertEqual(op.action, "create")
        self.assertTrue(op.path.endswith(".tsx"))
        self.assertTrue(op.content.endswith("\n"))


class TestEmitTree(unittest.TestCase):
    """Tree emission: renders ComponentNode tree into FileOps with scoped contexts."""

    def test_tree_yields_multiple_ops(self):
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
            template="dashboard_page.j2",
            layout="DashboardLayout",
        )
        root.add_child(child)

        resolve_imports(root)
        fileops = emit_tree(root)

        self.assertEqual(len(fileops), 2)

        paths = [f.path for f in fileops]
        self.assertIn("src/Page.tsx", paths)
        self.assertIn("src/KpiRow.tsx", paths)

    def test_root_composition_has_child_references(self):
        child = ComponentNode(
            component="KpiRow",
            file_path="src/components/KpiRow.tsx",
            props={"metrics": ["revenue", "growth"]},
            template="kpi_row.j2",
        )
        root = ComponentNode(
            component="Page",
            file_path="src/Page.tsx",
            props={},
            template="dashboard_page.j2",
            layout="DashboardLayout",
        )
        root.add_child(child)

        resolve_imports(root)
        fileops = emit_tree(root)

        root_content = [f for f in fileops if "Page.tsx" in f.path][0].content
        child_content = [f for f in fileops if "KpiRow.tsx" in f.path][0].content

        # Root references child via composition (tree-derived, not hardcoded)
        self.assertIn("KpiRow", root_content)
        self.assertIn("revenue", root_content)
        self.assertIn("growth", root_content)

        # Child still owns its own props in its own file
        self.assertIn("revenue", child_content)
        self.assertIn("growth", child_content)

        # Child import is derived from tree, not hardcoded
        self.assertIn("import { KpiRow } from './components/KpiRow'", root_content)

        # Root uses __COMPONENT_NAME__ from tree (dynamic export)
        self.assertIn("export const Page:", root_content)

    def test_no_prop_leakage_between_siblings(self):
        child_a = ComponentNode(
            component="KpiRow",
            file_path="src/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        child_b = ComponentNode(
            component="Timeseries",
            file_path="src/Timeseries.tsx",
            props={"metric": "growth"},
            template="timeseries.j2",
        )
        root = ComponentNode(
            component="Page",
            file_path="src/Page.tsx",
            props={},
            template="dashboard_page.j2",
        )
        root.add_child(child_a)
        root.add_child(child_b)

        resolve_imports(root)
        fileops = emit_tree(root)
        self.assertEqual(len(fileops), 3)

        kpi_content = [f for f in fileops if "KpiRow" in f.path][0].content
        ts_content = [f for f in fileops if "Timeseries" in f.path][0].content

        self.assertIn("revenue", kpi_content)
        self.assertIn("growth", ts_content)

        self.assertNotIn("growth", kpi_content)
        self.assertNotIn("revenue", ts_content)


class TestDeterminismWithTree(unittest.TestCase):
    """ComponentNode tree rendering is deterministic."""

    def test_tree_builder_deterministic(self):
        ast = {
            "nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}],
        }
        config = {
            "base_path": "src/",
            "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}],
        }
        t1 = build_component_tree(ast, config)
        t2 = build_component_tree(ast, config)
        self.assertEqual(t1.props, t2.props)
        self.assertEqual(t1.component, t2.component)
        self.assertEqual(len(t1.children), len(t2.children))

    def test_emit_tree_deterministic(self):
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
            template="dashboard_page.j2",
            layout="DashboardLayout",
        )
        root.add_child(child)

        resolve_imports(root)
        r1 = emit_tree(root)
        r2 = emit_tree(root)
        self.assertEqual(r1[0].content, r2[0].content)

    def test_byte_identical_output_same_tree(self):
        def build():
            child = ComponentNode(
                component="KpiRow",
                file_path="src/KpiRow.tsx",
                props={"metrics": ["revenue", "growth"]},
                template="kpi_row.j2",
            )
            root = ComponentNode(
                component="Page",
                file_path="src/Page.tsx",
                props={},
                template="dashboard_page.j2",
                layout="DashboardLayout",
            )
            root.add_child(child)
            resolve_imports(root)
            return root

        r1 = emit_tree(build())
        r2 = emit_tree(build())
        self.assertEqual(r1[0].content, r2[0].content)
        self.assertEqual(r1[1].content, r2[1].content)

    def test_deterministic_across_independent_calls(self):
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
            template="dashboard_page.j2",
        )
        root.add_child(child)

        resolve_imports(root)
        r1 = emit_tree(root)
        root_clone = ComponentNode(
            component="Page",
            file_path="src/Page.tsx",
            props={},
            template="dashboard_page.j2",
        )
        child_clone = ComponentNode(
            component="KpiRow",
            file_path="src/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        root_clone.add_child(child_clone)
        resolve_imports(root_clone)
        r2 = emit_tree(root_clone)

        self.assertEqual(r1[0].content, r2[0].content)


class TestCompositionIntegrity(unittest.TestCase):
    """Structural integrity of component trees and their output."""

    def setUp(self):
        self.root = ComponentNode(
            component="Dashboard",
            file_path="src/Dashboard.tsx",
            props={},
            template="dashboard_page.j2",
            layout="DashboardLayout",
            imports=["import { Card } from '@/components/ui/Card'"],
        )
        self.kpi = ComponentNode(
            component="KpiRow",
            file_path="src/components/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        self.ts = ComponentNode(
            component="Timeseries",
            file_path="src/components/Timeseries.tsx",
            props={"metric": "revenue"},
            template="timeseries.j2",
        )
        self.root.add_child(self.kpi)
        self.root.add_child(self.ts)
        resolve_imports(self.root)

    def test_no_orphan_children(self):
        for child in self.root.children:
            self.assertIsNotNone(child.parent)

    def test_all_children_render_to_files(self):
        fileops = emit_tree(self.root)
        paths = {f.path for f in fileops}
        self.assertIn("src/Dashboard.tsx", paths)
        self.assertIn("src/components/KpiRow.tsx", paths)
        self.assertIn("src/components/Timeseries.tsx", paths)

    def test_no_duplicate_fileops(self):
        fileops = emit_tree(self.root)
        paths = [f.path for f in fileops]
        self.assertEqual(len(paths), len(set(paths)))

    def test_no_dangling_composition_refs(self):
        fileops = emit_tree(self.root)
        root_content = next(f.content for f in fileops if "Dashboard" in f.path)

        for child in self.root.children:
            self.assertIn(child.component, root_content)


class TestRendererObservability(unittest.TestCase):
    """ComponentTree introspection and debugging support."""

    def test_render_tree_string_includes_all_nodes(self):
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
            template="dashboard_page.j2",
            layout="DashboardLayout",
        )
        root.add_child(child)

        tree_str = render_tree_string(root)
        self.assertIn("Page", tree_str)
        self.assertIn("KpiRow", tree_str)
        self.assertIn("dashboard_page.j2", tree_str)
        self.assertIn("kpi_row.j2", tree_str)

    def test_render_tree_string_shows_indentation(self):
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
            template="dashboard_page.j2",
        )
        root.add_child(child)

        tree_str = render_tree_string(root)
        lines = tree_str.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("Page"))
        self.assertTrue(lines[1].startswith("  KpiRow"))

    def test_props_visible_in_tree_string(self):
        node = ComponentNode(
            component="KpiRow",
            file_path="KpiRow.tsx",
            props={"metrics": ["revenue", "growth"]},
            template="kpi_row.j2",
        )
        tree_str = render_tree_string(node)
        self.assertIn("revenue", tree_str)
        self.assertIn("growth", tree_str)


if __name__ == "__main__":
    unittest.main()
