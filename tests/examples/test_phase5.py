"""Phase 5 tests: Slot-based composition validation layer.

Slot system adds structural composition contracts (slots) as a
pre-emission validation pass. SlotSpecs come from contract AST.
Binding is sequential greedy by component type match.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.renderer.component_node import (
    ComponentNode,
    SlotSpec,
    build_component_tree,
    emit_file,
    emit_tree,
    render_node,
    resolve_imports,
    resolve_slots,
    _build_node_context,
    _render_composition,
    _render_slot_composition,
)
from app.renderer.symbol_graph import validate_symbol_graph
from app.renderer.validators import validate_fileops
from app.renderer.base import FileOp


def _leaf_node(component: str, **overrides) -> ComponentNode:
    defaults = dict(
        component=component,
        file_path=f"src/{component}.tsx",
        props={},
        template=None,
    )
    defaults.update(overrides)
    return ComponentNode(**defaults)


def _root_node(component: str = "Page", **overrides) -> ComponentNode:
    defaults = dict(
        component=component,
        file_path=f"src/{component}.tsx",
        props={},
        template="dashboard_page.j2",
        layout="DashboardLayout",
    )
    defaults.update(overrides)
    return ComponentNode(**defaults)


class TestSlotSpec(unittest.TestCase):
    """SlotSpec dataclass and ComponentNode slot fields."""

    def test_slot_spec_defaults(self):
        spec = SlotSpec(name="body", allowed_types=["KpiRow", "Timeseries"])
        self.assertEqual(spec.name, "body")
        self.assertEqual(spec.allowed_types, ["KpiRow", "Timeseries"])
        self.assertFalse(spec.required)
        self.assertEqual(spec.allowed, "single")

    def test_component_node_slots_default_none(self):
        node = _leaf_node("KpiRow")
        self.assertIsNone(node.slots)
        self.assertIsNone(node.slot_bindings)

    def test_component_node_with_slots(self):
        slots = [
            SlotSpec(name="header", allowed_types=["Header"], required=True),
            SlotSpec(name="body", allowed_types=["KpiRow"], allowed="multiple"),
        ]
        node = _root_node(slots=slots)
        self.assertEqual(len(node.slots), 2)
        self.assertEqual(node.slots[0].name, "header")
        self.assertEqual(node.slots[1].allowed, "multiple")


class TestNoSlotsCompat(unittest.TestCase):
    """Phase 4 compatibility: nodes without slots behave identically."""

    def test_resolve_slots_noop_on_none(self):
        child = _leaf_node("KpiRow", template="kpi_row.j2",
                           props={"metrics": ["revenue"]})
        root = _root_node(children=[child])
        child.parent = root

        bindings_before = root.slot_bindings
        resolve_slots(root)
        self.assertIsNone(bindings_before)
        self.assertIsNone(root.slot_bindings)

    def test_composition_uses_legacy_path(self):
        child = _leaf_node("KpiRow", template="kpi_row.j2",
                           props={"metrics": ["revenue"]})
        root = _root_node(children=[child])
        child.parent = root
        resolve_imports(root)

        legacy = _render_composition(root.children)
        context = _build_node_context(root)
        self.assertEqual(context["COMPOSITION"], legacy)

    def test_no_slots_emit_unchanged(self):
        child = _leaf_node("KpiRow", template="kpi_row.j2",
                           props={"metrics": ["revenue"]})
        root = _root_node(component="Dashboard", children=[child])
        child.parent = root
        resolve_imports(root)

        ops = emit_tree(root)
        self.assertEqual(len(ops), 2)  # root + child

    def test_no_slots_deterministic(self):
        def build():
            child = _leaf_node("KpiRow", template="kpi_row.j2",
                               props={"metrics": ["revenue"]})
            root = _root_node(children=[child])
            child.parent = root
            return root

        t1 = build()
        t2 = build()
        resolve_imports(t1)
        resolve_imports(t2)

        r1 = emit_tree(t1)
        r2 = emit_tree(t2)
        self.assertEqual(r1[0].content, r2[0].content)


class TestResolveSlots(unittest.TestCase):
    """resolve_slots: sequential greedy binding by type."""

    def test_valid_single_slot_binding(self):
        child = _leaf_node("KpiRow")
        slot = SlotSpec(name="body", allowed_types=["KpiRow"], required=True)
        root = _root_node(children=[child], slots=[slot])
        child.parent = root

        resolve_slots(root)
        self.assertIn("body", root.slot_bindings)
        self.assertIs(root.slot_bindings["body"], child)

    def test_invalid_slot_type_raises(self):
        child = _leaf_node("Timeseries")
        slot = SlotSpec(name="body", allowed_types=["KpiRow"], required=True)
        root = _root_node(children=[child], slots=[slot])
        child.parent = root

        with self.assertRaises(RuntimeError) as ctx:
            resolve_slots(root)
        self.assertIn("Phase5SlotViolation", str(ctx.exception))

    def test_required_slot_empty_raises(self):
        slot = SlotSpec(name="body", allowed_types=["KpiRow"], required=True)
        root = _root_node(children=[], slots=[slot])

        with self.assertRaises(RuntimeError) as ctx:
            resolve_slots(root)
        self.assertIn("Phase5SlotViolation", str(ctx.exception))

    def test_multiple_slot_collects_all_matching(self):
        child_a = _leaf_node("KpiRow", props={"metrics": ["a"]})
        child_b = _leaf_node("KpiRow", props={"metrics": ["b"]})
        slot = SlotSpec(name="kpis", allowed_types=["KpiRow"],
                        required=True, allowed="multiple")
        root = _root_node(children=[child_a, child_b], slots=[slot])
        for c in [child_a, child_b]:
            c.parent = root

        resolve_slots(root)
        bound = root.slot_bindings["kpis"]
        self.assertEqual(len(bound), 2)
        self.assertIs(bound[0], child_a)
        self.assertIs(bound[1], child_b)

    def test_multiple_slot_empty_not_required(self):
        slot = SlotSpec(name="kpis", allowed_types=["KpiRow"],
                        required=False, allowed="multiple")
        root = _root_node(children=[], slots=[slot])
        resolve_slots(root)
        self.assertEqual(root.slot_bindings["kpis"], [])

    def test_sequential_binding_order(self):
        child_a = _leaf_node("Header")
        child_b = _leaf_node("KpiRow")
        child_c = _leaf_node("Footer")
        slots = [
            SlotSpec(name="header", allowed_types=["Header"], required=True),
            SlotSpec(name="body", allowed_types=["KpiRow"], required=True),
            SlotSpec(name="footer", allowed_types=["Footer"], required=True),
        ]
        root = _root_node(children=[child_a, child_b, child_c], slots=slots)
        for c in [child_a, child_b, child_c]:
            c.parent = root

        resolve_slots(root)
        self.assertIs(root.slot_bindings["header"], child_a)
        self.assertIs(root.slot_bindings["body"], child_b)
        self.assertIs(root.slot_bindings["footer"], child_c)

    def test_unassigned_child_raises(self):
        child_a = _leaf_node("KpiRow")
        child_b = _leaf_node("RandomWidget")
        slot = SlotSpec(name="body", allowed_types=["KpiRow"], required=True)
        root = _root_node(children=[child_a, child_b], slots=[slot])
        for c in [child_a, child_b]:
            c.parent = root

        with self.assertRaises(RuntimeError) as ctx:
            resolve_slots(root)
        self.assertIn("Phase5SlotViolation", str(ctx.exception))
        self.assertIn("unassigned child", str(ctx.exception))

    def test_single_slot_consumes_one_child_only(self):
        child_a = _leaf_node("KpiRow")
        child_b = _leaf_node("KpiRow")
        slot = SlotSpec(name="body", allowed_types=["KpiRow"], required=True)
        # second child has no matching slot → unassigned error
        root = _root_node(children=[child_a, child_b], slots=[slot])
        for c in [child_a, child_b]:
            c.parent = root

        with self.assertRaises(RuntimeError) as ctx:
            resolve_slots(root)
        self.assertIn("unassigned child", str(ctx.exception))

    def test_multiple_slot_collects_all_regardless_of_position(self):
        child_a = _leaf_node("KpiRow")
        child_b = _leaf_node("Timeseries")
        child_c = _leaf_node("KpiRow")
        slot = SlotSpec(name="body", allowed_types=["KpiRow", "Timeseries"],
                        allowed="multiple", required=True)
        root = _root_node(children=[child_a, child_b, child_c], slots=[slot])
        for c in [child_a, child_b, child_c]:
            c.parent = root

        # multiple collects ALL children that match any allowed type
        resolve_slots(root)
        bound = root.slot_bindings["body"]
        self.assertEqual(len(bound), 3)
        self.assertIs(bound[0], child_a)
        self.assertIs(bound[1], child_b)
        self.assertIs(bound[2], child_c)


class TestSlotComposition(unittest.TestCase):
    """_render_slot_composition: slot-ordered JSX reference tags."""

    def test_slot_composition_follows_slot_order(self):
        kpi = _leaf_node("KpiRow", props={"metrics": ["revenue"]})
        header = _leaf_node("Header", props={"title": "Dashboard"})
        slots = [
            SlotSpec(name="header", allowed_types=["Header"], required=True),
            SlotSpec(name="body", allowed_types=["KpiRow"], required=True),
        ]
        root = _root_node(children=[kpi, header], slots=slots)
        kpi.parent = root
        header.parent = root

        resolve_slots(root)
        result = _render_slot_composition(root)

        # Header comes first (slot order), KpiRow second
        lines = [l.strip() for l in result.split("\n")]
        self.assertIn("<Header", lines[0])
        self.assertIn("<KpiRow", lines[1])

    def test_slot_composition_ignores_unbound_nonrequired(self):
        kpi = _leaf_node("KpiRow", props={"metrics": ["revenue"]})
        slots = [
            SlotSpec(name="header", allowed_types=["Header"], required=False),
            SlotSpec(name="body", allowed_types=["KpiRow"], required=True),
        ]
        root = _root_node(children=[kpi], slots=slots)
        kpi.parent = root

        resolve_slots(root)
        result = _render_slot_composition(root)
        self.assertNotIn("header", result.lower())
        self.assertIn("KpiRow", result)

    def test_required_empty_slot_emits_placeholder(self):
        slots = [
            SlotSpec(name="header", allowed_types=["Header"], required=True),
            SlotSpec(name="body", allowed_types=["KpiRow"], required=False),
        ]
        root = _root_node(children=[], slots=slots)
        # Manually set slot_bindings to test composition rendering
        # bypassing resolve_slots (which would reject required empty)
        root.slot_bindings = {}
        result = _render_slot_composition(root)
        self.assertIn("slot: header", result)
        self.assertNotIn("slot: body", result)

    def test_multiple_slot_composition_preserves_insertion_order(self):
        child_a = _leaf_node("KpiRow", props={"metrics": ["a"]})
        child_b = _leaf_node("KpiRow", props={"metrics": ["b"]})
        slot = SlotSpec(name="kpis", allowed_types=["KpiRow"],
                        allowed="multiple", required=True)
        root = _root_node(children=[child_a, child_b], slots=[slot])
        for c in [child_a, child_b]:
            c.parent = root

        resolve_slots(root)
        result = _render_slot_composition(root)
        lines = [l.strip() for l in result.split("\n")]
        self.assertIn("a", lines[0])
        self.assertIn("b", lines[1])


class TestInvariant2(unittest.TestCase):
    """emit_file must not iterate node.children if slots exist."""

    def test_slots_without_bindings_raises(self):
        child = _leaf_node("KpiRow", template="kpi_row.j2")
        slots = [SlotSpec(name="body", allowed_types=["KpiRow"], required=True)]
        root = _root_node(children=[child], slots=slots, template="dashboard_page.j2")
        child.parent = root
        resolve_imports(root)
        # _build_node_context detects slots without bindings
        with self.assertRaises(RuntimeError) as ctx:
            emit_file(root)
        self.assertIn("has slots but no slot_bindings", str(ctx.exception))

    def test_slots_with_bindings_emits(self):
        child = _leaf_node("KpiRow", template="kpi_row.j2",
                           props={"metrics": ["revenue"]})
        slots = [SlotSpec(name="body", allowed_types=["KpiRow"], required=True)]
        root = _root_node(children=[child], slots=slots)
        child.parent = root

        resolve_imports(root)
        resolve_slots(root)

        ops = emit_tree(root)
        self.assertEqual(len(ops), 2)  # root + child
        root_content = [f for f in ops if "Page" in f.path][0].content
        self.assertIn("KpiRow", root_content)


class TestFullPipeline(unittest.TestCase):
    """End-to-end: build → imports → slots → emit → SymbolGraph."""

    def _make_pipeline(self, ast: dict, renderer: dict, ctx=None):
        root = build_component_tree(ast, renderer, example_context=ctx)
        resolve_imports(root)
        resolve_slots(root)
        return root

    def test_ast_slot_specs_flow_into_tree(self):
        ast = {
            "layout": "AnalyticsGrid",
            "nodes": [
                {
                    "type": "Dashboard",
                    "props": {},
                    "slots": [
                        {"name": "body", "allowed_types": ["KpiRow"], "required": True},
                    ],
                },
            ],
        }
        renderer = {
            "base_path": "src/pages/dashboard/",
            "files": [
                {"path": "Dashboard.tsx", "template": "dashboard_page.j2"},
                {"path": "components/KpiRow.tsx", "template": "kpi_row.j2"},
            ],
        }

        root = self._make_pipeline(ast, renderer)
        self.assertIsNotNone(root.slots)
        self.assertEqual(len(root.slots), 1)
        self.assertEqual(root.slots[0].name, "body")
        self.assertEqual(root.slots[0].allowed_types, ["KpiRow"])

    def test_pipeline_with_slots_passes_symbolgraph(self):
        ast = {
            "layout": "AnalyticsGrid",
            "nodes": [
                {
                    "type": "Dashboard",
                    "props": {},
                    "slots": [
                        {"name": "body", "allowed_types": ["KpiRow"], "required": True},
                    ],
                },
            ],
        }
        renderer = {
            "base_path": "src/pages/dashboard/",
            "files": [
                {"path": "Dashboard.tsx", "template": "dashboard_page.j2"},
                {"path": "components/KpiRow.tsx", "template": "kpi_row.j2"},
            ],
        }

        root = self._make_pipeline(ast, renderer)
        ops = emit_tree(root)

        ok, reason = validate_symbol_graph(root, renderer)
        self.assertTrue(ok, msg=reason)

        ok, vreason = validate_fileops(ops)
        self.assertTrue(ok, msg=vreason)

    def test_pipeline_version_no_slots_unchanged(self):
        ast = {
            "layout": "AnalyticsGrid",
            "nodes": [
                {"type": "KpiRow", "props": {"metrics": ["revenue"]}},
                {"type": "Timeseries", "props": {"metric": "revenue"}},
            ],
        }
        renderer = {
            "base_path": "src/pages/dashboard/",
            "files": [
                {"path": "SalesOverview.tsx", "template": "dashboard_page.j2"},
                {"path": "components/KpiRow.tsx", "template": "kpi_row.j2"},
                {"path": "components/Timeseries.tsx", "template": "timeseries.j2"},
            ],
        }

        root = self._make_pipeline(ast, renderer)
        self.assertIsNone(root.slots)
        self.assertIsNone(root.slot_bindings)

        ops = emit_tree(root)
        self.assertEqual(len(ops), 3)

        ok, reason = validate_symbol_graph(root, renderer)
        self.assertTrue(ok, msg=reason)


class TestClosureSlotSoundness(unittest.TestCase):
    """Final closure tests: structural slot integrity + determinism."""

    def test_phase5_slot_system_end_to_end(self):
        node = ComponentNode(
            component="Dashboard",
            template="dashboard_page.j2",
            file_path="out/Dashboard.tsx",
            slots=[
                SlotSpec(name="header", allowed_types=["Header"],
                         required=True, allowed="single"),
                SlotSpec(name="body", allowed_types=["Chart", "Table"],
                         required=True, allowed="multiple"),
            ],
        )
        node.add_child(ComponentNode(
            component="Header",
            file_path="out/Header.tsx",
            props={"title": "Dashboard"},
        ))
        node.add_child(ComponentNode(
            component="Chart",
            file_path="out/Chart.tsx",
            props={"metric": "revenue"},
        ))
        node.add_child(ComponentNode(
            component="Table",
            file_path="out/Table.tsx",
            props={"columns": ["Name", "Value"]},
        ))

        resolve_imports(node)
        resolve_slots(node)

        self.assertIsNotNone(node.slot_bindings)
        self.assertEqual(set(node.slot_bindings.keys()), {"header", "body"})
        self.assertEqual(node.slot_bindings["header"].component, "Header")
        body = node.slot_bindings["body"]
        self.assertEqual([c.component for c in body], ["Chart", "Table"])

        fileop = emit_file(node)
        self.assertIsNotNone(fileop)
        self.assertIn("Header", fileop.content)
        self.assertIn("Chart", fileop.content)
        self.assertIn("Table", fileop.content)

    def test_phase5_slot_mismatch_fails(self):
        node = ComponentNode(
            component="Dashboard",
            template="dashboard_page.j2",
            file_path="out/Dashboard.tsx",
            slots=[
                SlotSpec(name="header", allowed_types=["Header"],
                         required=True, allowed="single"),
            ],
        )
        node.add_child(ComponentNode(
            component="Chart",  # NOT valid for "header" slot
            file_path="out/Chart.tsx",
        ))

        resolve_imports(node)
        with self.assertRaises(RuntimeError) as ctx:
            resolve_slots(node)
        self.assertIn("Phase5", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
