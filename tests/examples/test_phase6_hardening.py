"""Phase 6.1 — Compiler Hardening Test Suite.

Validates: pipeline order enforcement, mode enforcement, single mode
authority, IR consistency, snapshot safety, regression locks.
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
)
from app.renderer.file_renderer import FileRenderer
from app.renderer.compiler import (
    CompilerConfig,
    CompilerMode,
    CompilerIRSnapshot,
    validate_compiler_contract,
    validate_compiler_ir,
    capture_ir_snapshot,
)
from app.renderer.base import FileOp


def _make_simple_ast_nodes(*types: str) -> list[dict]:
    return [{"type": t, "props": {}} for t in types]


def _make_renderer_config(
    root_name: str = "Dashboard",
    child_names: list[str] | None = None,
    base_path: str = "src/pages/",
) -> dict:
    files = [{"path": f"{root_name}.tsx", "template": "dashboard_page.j2"}]
    if child_names:
        for name in child_names:
            files.append({"path": f"components/{name}.tsx", "template": "kpi_row.j2"})
    return {"base_path": base_path, "files": files}


def _build_full_root(
    component: str = "Dashboard",
    slot_specs: list[SlotSpec] | None = None,
    children: list[ComponentNode] | None = None,
) -> ComponentNode:
    node = ComponentNode(
        component=component,
        file_path=f"src/pages/{component}.tsx",
        props={},
        template="dashboard_page.j2",
        layout="DashboardLayout",
        slots=slot_specs,
    )
    if children:
        for c in children:
            node.add_child(c)
    return node


# ═══════════════════════════════════════════════════════════════════════
# 1. PIPELINE ORDER ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════


class TestPipelineOrder(unittest.TestCase):
    """validate_compiler_ir must run before emit_tree, never after."""

    def test_validate_ir_runs_before_emit_only(self):
        node = _build_full_root(
            component="Dashboard",
            slot_specs=[SlotSpec(name="body", allowed_types=["KpiRow"], required=True)],
            children=[ComponentNode(component="KpiRow", file_path="src/pages/components/KpiRow.tsx")],
        )
        resolve_imports(node)
        resolve_slots(node)

        # IR validation must pass before emission
        validate_compiler_ir(node, CompilerConfig(mode="enforced"))

        # Only after IR validation passes does emit run
        op = emit_file(node)
        self.assertIsNotNone(op)
        self.assertIn("KpiRow", op.content)

    def test_emit_tree_never_runs_on_invalid_ir(self):
        node = _build_full_root(
            component="Dashboard",
            slot_specs=[SlotSpec(name="body", allowed_types=["KpiRow"], required=True)],
            children=[ComponentNode(component="KpiRow", file_path="src/pages/components/KpiRow.tsx")],
        )
        resolve_imports(node)
        resolve_slots(node)

        # Corrupt IR: remove slot_bindings
        node.slot_bindings = None

        with self.assertRaises(RuntimeError) as ctx:
            validate_compiler_ir(node, CompilerConfig(mode="enforced"))
        self.assertIn("IncompleteIR", str(ctx.exception))

        # emit_tree must NOT proceed
        with self.assertRaises(RuntimeError):
            emit_tree(node)


# ═══════════════════════════════════════════════════════════════════════
# 2. MODE ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════


class TestModeEnforcement(unittest.TestCase):
    """strict vs enforced vs legacy behavior."""

    def test_strict_rejects_missing_slot_bindings(self):
        node = _build_full_root(
            component="Dashboard",
            slot_specs=[SlotSpec(name="body", allowed_types=["KpiRow"], required=True)],
            children=[ComponentNode(component="KpiRow", file_path="src/pages/components/KpiRow.tsx")],
        )
        resolve_imports(node)
        # Deliberately skip resolve_slots

        with self.assertRaises(RuntimeError) as ctx:
            validate_compiler_ir(node, CompilerConfig(mode="strict"))
        self.assertIn("IncompleteIR", str(ctx.exception))

    def test_enforced_rejects_orphan_child(self):
        node = _build_full_root(
            component="Dashboard",
            slot_specs=[SlotSpec(name="header", allowed_types=["Header"], required=True)],
            children=[ComponentNode(component="Chart", file_path="src/pages/components/Chart.tsx")],
        )
        resolve_imports(node)

        with self.assertRaises(RuntimeError) as ctx:
            resolve_slots(node)
        self.assertIn("Phase5SlotViolation", str(ctx.exception))

    def test_legacy_allows_partial_ir(self):
        node = _build_full_root(
            component="Dashboard",
            children=[ComponentNode(
                component="KpiRow",
                file_path="src/pages/components/KpiRow.tsx",
                props={"metrics": ["revenue"]},
                template="kpi_row.j2",
            )],
        )
        resolve_imports(node)
        # No resolve_slots, no slot specs — legacy path

        # IR validation in legacy mode must skip
        validate_compiler_ir(node, CompilerConfig(mode="legacy"))

        op = emit_file(node)
        self.assertIsNotNone(op)
        self.assertIn("KpiRow", op.content)

    def test_legacy_allows_none_resolved_imports(self):
        """In legacy mode, None resolved_imports must not block emit."""
        node = _build_full_root(
            component="Dashboard",
        )
        # Deliberately skip resolve_imports and resolve_slots

        validate_compiler_ir(node, CompilerConfig(mode="legacy"))

        # emit_file should still fail (MissingImportResolution is hard even in legacy)
        with self.assertRaises(RuntimeError) as ctx:
            emit_file(node)
        self.assertIn("MissingImportResolution", str(ctx.exception))

    def test_enforced_rejects_single_slot_fallback_input(self):
        """Contract gate in enforced mode must reject single-slot fallback input."""
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config = _make_renderer_config(root_name="Dashboard")

        with self.assertRaises(RuntimeError) as ctx:
            validate_compiler_contract(ast, config, CompilerConfig(mode="enforced"))
        self.assertIn("ContractViolation", str(ctx.exception))


# ═══════════════════════════════════════════════════════════════════════
# 3. SINGLE MODE AUTHORITY
# ═══════════════════════════════════════════════════════════════════════


class TestSingleModeAuthority(unittest.TestCase):
    """FileRenderer must NOT create its own CompilerConfig."""

    def test_file_renderer_no_internal_config_creation(self):
        renderer = FileRenderer()
        with self.assertRaises(RuntimeError) as ctx:
            renderer.render(
                {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]},
                {"base_path": "src/", "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}]},
            )
        self.assertIn("requires explicit compiler_config", str(ctx.exception))

    def test_mode_propagation_consistency(self):
        cc = CompilerConfig(mode="strict")
        self.assertEqual(cc.mode, "strict")
        self.assertEqual(cc.mode, CompilerConfig(mode="strict").mode)


# ═══════════════════════════════════════════════════════════════════════
# 4. IR CONSISTENCY ATTACK
# ═══════════════════════════════════════════════════════════════════════


class TestIRConsistency(unittest.TestCase):
    """Attack IR state with invalid patterns — system must reject."""

    def test_orphan_child_detection(self):
        node = _build_full_root(
            component="Dashboard",
            slot_specs=[SlotSpec(name="body", allowed_types=["KpiRow"], required=True)],
            children=[
                ComponentNode(component="KpiRow", file_path="src/pages/components/KpiRow.tsx"),
                ComponentNode(component="Orphan", file_path="src/pages/components/Orphan.tsx"),
            ],
        )
        resolve_imports(node)

        with self.assertRaises(RuntimeError) as ctx:
            resolve_slots(node)
        self.assertIn("Phase5SlotViolation", str(ctx.exception))
        self.assertIn("unassigned child", str(ctx.exception))

    def test_slot_binding_completeness_enforced(self):
        node = _build_full_root(
            component="Dashboard",
            slot_specs=[
                SlotSpec(name="header", allowed_types=["Header"], required=True),
                SlotSpec(name="body", allowed_types=["KpiRow"], required=True),
            ],
            children=[
                ComponentNode(component="Header", file_path="src/pages/components/Header.tsx"),
                ComponentNode(component="KpiRow", file_path="src/pages/components/KpiRow.tsx"),
            ],
        )
        resolve_imports(node)
        resolve_slots(node)

        # Must pass validation
        validate_compiler_ir(node, CompilerConfig(mode="enforced"))

        # All children must be in slot_bindings (check by component name)
        bound_names: set[str] = set()
        for binding in node.slot_bindings.values():
            if isinstance(binding, list):
                bound_names.update(c.component for c in binding)
            else:
                bound_names.add(binding.component)
        self.assertEqual(bound_names, {c.component for c in node.children})

    def test_partial_slot_bindings_rejected_enforced(self):
        node = _build_full_root(
            component="Dashboard",
            slot_specs=[
                SlotSpec(name="header", allowed_types=["Header"], required=True),
                SlotSpec(name="body", allowed_types=["KpiRow"], required=True),
            ],
            children=[
                ComponentNode(component="Header", file_path="src/pages/components/Header.tsx"),
                ComponentNode(component="KpiRow", file_path="src/pages/components/KpiRow.tsx"),
            ],
        )
        resolve_imports(node)
        resolve_slots(node)

        # Manually corrupt: remove one binding
        del node.slot_bindings["body"]

        with self.assertRaises(RuntimeError) as ctx:
            validate_compiler_ir(node, CompilerConfig(mode="enforced"))
        self.assertIn("IncompleteIR", str(ctx.exception))
        self.assertIn("missing bindings", str(ctx.exception))

    def test_slot_binding_without_slots_rejected(self):
        node = _build_full_root(component="Dashboard")
        node.slot_bindings = {"body": ComponentNode(component="KpiRow", file_path="KpiRow.tsx")}
        resolve_imports(node)

        with self.assertRaises(RuntimeError) as ctx:
            validate_compiler_ir(node, CompilerConfig(mode="strict"))
        self.assertIn("IncompleteIR", str(ctx.exception))
        self.assertIn("slot_bindings", str(ctx.exception))

    def test_contract_missing_nodes_rejected(self):
        ast: dict = {}
        config = {"base_path": "src/", "files": [{"path": "Test.tsx", "template": "kpi_row.j2"}]}
        with self.assertRaises(RuntimeError) as ctx:
            validate_compiler_contract(ast, config, CompilerConfig(mode="strict"))
        self.assertIn("ContractViolation", str(ctx.exception))

    def test_contract_missing_files_rejected(self):
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config: dict = {"base_path": "src/"}
        with self.assertRaises(RuntimeError) as ctx:
            validate_compiler_contract(ast, config, CompilerConfig(mode="strict"))
        self.assertIn("ContractViolation", str(ctx.exception))


# ═══════════════════════════════════════════════════════════════════════
# 5. SNAPSHOT SAFETY
# ═══════════════════════════════════════════════════════════════════════


class TestSnapshotSafety(unittest.TestCase):
    """Snapshot is observation-only, never mutates, never validates."""

    def test_snapshot_does_not_mutate_ir(self):
        node = _build_full_root(
            component="Dashboard",
            slot_specs=[SlotSpec(name="body", allowed_types=["KpiRow"], required=True)],
            children=[ComponentNode(component="KpiRow", file_path="src/pages/components/KpiRow.tsx")],
        )
        resolve_imports(node)
        resolve_slots(node)

        snapshot = capture_ir_snapshot(node)

        original_bindings = dict(node.slot_bindings)
        original_imports = {n.component: n.resolved_imports for n in [node] + node.children}

        self.assertEqual(snapshot.root_component, "Dashboard")
        self.assertEqual(snapshot.node_count, 2)

        # Snapshot state must match IR before any mutation
        self.assertEqual(
            snapshot.slot_bindings_state["Dashboard"]["body"],
            "KpiRow",
        )

    def test_snapshot_on_invalid_ir_does_not_raise(self):
        node = _build_full_root(component="Dashboard")
        # IR is incomplete (no resolved_imports, no slots)

        # Snapshot must never raise — even on invalid IR
        snapshot = capture_ir_snapshot(node)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.root_component, "Dashboard")
        self.assertIsNone(snapshot.resolved_imports_state["Dashboard"])
        self.assertIsNone(snapshot.slot_bindings_state["Dashboard"])

    def test_snapshot_to_dict_roundtrip(self):
        child = ComponentNode(
            component="KpiRow",
            file_path="src/pages/components/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            imports=["import React from 'react'"],
            template="kpi_row.j2",
        )
        node = _build_full_root(
            component="Dashboard",
            slot_specs=[SlotSpec(name="body", allowed_types=["KpiRow"], required=True)],
            children=[child],
        )
        resolve_imports(node)
        resolve_slots(node)

        snapshot = capture_ir_snapshot(node)
        d = snapshot.to_dict()

        self.assertEqual(d["root_component"], "Dashboard")
        self.assertEqual(d["node_count"], 2)
        self.assertIn("KpiRow", d["slot_bindings_state"]["Dashboard"]["body"])


# ═══════════════════════════════════════════════════════════════════════
# 6. REGRESSION LOCK
# ═══════════════════════════════════════════════════════════════════════


class TestRegressionLock(unittest.TestCase):
    """Phase 4/5 behavior must be identical with legacy CompilerConfig."""

    def test_phase4_behavior_intact(self):
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue", "growth"]}}]}
        config = {
            "base_path": "src/",
            "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}],
        }
        renderer = FileRenderer()
        cc = CompilerConfig(mode="legacy")

        fileops = renderer.render(ast, config, compiler_config=cc)
        self.assertEqual(len(fileops), 1)
        content = fileops[0].content
        self.assertIn("KpiRow", content)
        self.assertIn("revenue", content)
        self.assertIn("growth", content)

    def test_phase4_determinism_preserved(self):
        ast = {"nodes": [{"type": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        config = {
            "base_path": "src/",
            "files": [{"path": "KpiRow.tsx", "template": "kpi_row.j2"}],
        }
        renderer = FileRenderer()
        cc = CompilerConfig(mode="legacy")

        r1 = renderer.render(ast, config, compiler_config=cc)
        r2 = renderer.render(ast, config, compiler_config=cc)
        self.assertEqual(r1[0].content, r2[0].content)

    def test_phase5_slot_behavior_unchanged(self):
        node = ComponentNode(
            component="Dashboard",
            template="dashboard_page.j2",
            file_path="out/Dashboard.tsx",
            slots=[
                SlotSpec(name="header", allowed_types=["Header"], required=True, allowed="single"),
                SlotSpec(name="body", allowed_types=["Chart", "Table"], required=True, allowed="multiple"),
            ],
        )
        node.add_child(ComponentNode(component="Header", file_path="out/Header.tsx"))
        node.add_child(ComponentNode(component="Chart", file_path="out/Chart.tsx"))
        node.add_child(ComponentNode(component="Table", file_path="out/Table.tsx"))

        resolve_imports(node)
        resolve_slots(node)

        # Verify slot bindings (Phase 5 contract)
        self.assertEqual(node.slot_bindings["header"].component, "Header")
        self.assertEqual([c.component for c in node.slot_bindings["body"]], ["Chart", "Table"])

        # IR validation in strict mode must pass
        validate_compiler_ir(node, CompilerConfig(mode="strict"))

        fileop = emit_file(node)
        self.assertIsNotNone(fileop)
        self.assertIn("Header", fileop.content)
        self.assertIn("Chart", fileop.content)

    def test_phase5_slot_mismatch_still_fails_in_strict(self):
        node = ComponentNode(
            component="Dashboard",
            template="dashboard_page.j2",
            file_path="out/Dashboard.tsx",
            slots=[SlotSpec(name="header", allowed_types=["Header"], required=True, allowed="single")],
        )
        node.add_child(ComponentNode(component="Chart", file_path="out/Chart.tsx"))

        resolve_imports(node)
        with self.assertRaises(RuntimeError) as ctx:
            resolve_slots(node)
        self.assertIn("Phase5SlotViolation", str(ctx.exception))


# ═══════════════════════════════════════════════════════════════════════
# 7. GOLDEN PIPELINE
# ═══════════════════════════════════════════════════════════════════════


class TestGoldenPipeline(unittest.TestCase):
    """End-to-end determinism: same input → identical FileOps."""

    def _run_file_renderer(self, cc: CompilerConfig) -> list[FileOp]:
        ast = {
            "nodes": [
                {
                    "type": "Dashboard",
                    "props": {},
                    "slots": [
                        {"name": "header", "allowed_types": ["Header"], "required": True},
                        {"name": "body", "allowed_types": ["KpiRow"], "required": True, "allowed": "multiple"},
                    ],
                },
            ],
        }
        config = {
            "base_path": "src/pages/dashboard/",
            "files": [
                {"path": "Dashboard.tsx", "template": "dashboard_page.j2"},
                {"path": "components/Header.tsx", "template": "kpi_row.j2"},
                {"path": "components/KpiRow.tsx", "template": "kpi_row.j2"},
            ],
        }
        return FileRenderer().render(ast, config, compiler_config=cc)

    def test_full_compiler_pipeline_determinism(self):
        cc = CompilerConfig(mode="strict")
        run1 = self._run_file_renderer(cc)
        run2 = self._run_file_renderer(cc)
        self.assertEqual(len(run1), len(run2))
        for f1, f2 in zip(run1, run2):
            self.assertEqual(f1.action, f2.action)
            self.assertEqual(f1.path, f2.path)
            self.assertEqual(f1.content, f2.content)

    def test_full_compiler_pipeline_strict_passes(self):
        cc = CompilerConfig(mode="strict")
        fileops = self._run_file_renderer(cc)
        self.assertGreater(len(fileops), 0)
        root_op = next(fop for fop in fileops if fop.path.endswith("Dashboard.tsx"))
        self.assertIn("Dashboard", root_op.content)

    def test_full_compiler_pipeline_enforced_passes(self):
        cc = CompilerConfig(mode="enforced")
        fileops = self._run_file_renderer(cc)
        self.assertGreater(len(fileops), 0)

    def test_full_compiler_pipeline_legacy_passes(self):
        cc = CompilerConfig(mode="legacy")
        fileops = self._run_file_renderer(cc)
        self.assertGreater(len(fileops), 0)


# ═══════════════════════════════════════════════════════════════════════
# 8. BONUS: IR Serialization Stability
# ═══════════════════════════════════════════════════════════════════════


class TestIRSerialization(unittest.TestCase):
    """IR snapshot serialization roundtrip stability."""

    def test_ir_serialization_stability(self):
        child = ComponentNode(
            component="KpiRow",
            file_path="src/pages/components/KpiRow.tsx",
            props={"metrics": ["revenue"]},
            template="kpi_row.j2",
        )
        node = _build_full_root(
            component="Dashboard",
            slot_specs=[SlotSpec(name="body", allowed_types=["KpiRow"], required=True)],
            children=[child],
        )
        resolve_imports(node)
        resolve_slots(node)

        # Capture → serialize (to_dict) → rehydrate (from dict)
        snapshot = capture_ir_snapshot(node)
        d = snapshot.to_dict()

        # Reconstruct by passing dict to CompilerIRSnapshot constructor
        rehydrated = CompilerIRSnapshot(**d)
        self.assertEqual(snapshot.root_component, rehydrated.root_component)
        self.assertEqual(snapshot.node_count, rehydrated.node_count)
        self.assertEqual(
            snapshot.resolved_imports_state,
            rehydrated.resolved_imports_state,
        )
        self.assertEqual(
            snapshot.slot_bindings_state,
            rehydrated.slot_bindings_state,
        )
        self.assertEqual(snapshot.slots_declared, rehydrated.slots_declared)
        self.assertEqual(snapshot.children_count, rehydrated.children_count)


if __name__ == "__main__":
    unittest.main()
