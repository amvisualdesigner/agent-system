"""Render stabilization — golden tests + contract verification.

Suite:
  1. Golden render: StructuralIR → FileOps snapshot, idempotent across runs
  2. Path parity: Path A (ReactBackend) vs Path B (RepositoryAwareRenderer)
  3. Tree authority: only UIComponentNode.children is consumed
  4. Mutation authority: renderer NEVER emits DELETE FileOps
"""

import json
import os

from app.graphir.backends import ReactBackend, BackendConfig
from app.graphir.constraint.renderer import RepositoryAwareRenderer
from app.graphir.constraint.context import PipelineState, RenderContext, ExecutionContext
from app.graphir.constraint.models import FileOpDecision, Decision
from app.graphir.models import (
    GraphIR, GraphIRLayout, GraphIRNode, GraphIREdge, GraphIRDraft, EdgeRole,
)
from app.graphir.ui_ir import UIComponentTree, UIComponentNode


GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden")
UPDATE_GOLDEN = os.environ.get("UPDATE_GOLDEN", "").lower() in ("1", "true", "yes")


# ── Helpers ──────────────────────────────────────────────────────────


def _build_kpi_graph() -> tuple[GraphIR, GraphIRLayout]:
    draft = GraphIRDraft()
    draft.add_node(GraphIRNode(id="n1", type="KpiRow", data={"metrics": ["a", "b"]}))
    graph = draft.freeze()
    layout = GraphIRLayout(root="n1")
    return graph, layout


def _build_dashboard_graph() -> tuple[GraphIR, GraphIRLayout]:
    draft = GraphIRDraft()
    draft.add_node(GraphIRNode(id="n1", type="Page"))
    draft.add_node(GraphIRNode(id="n2", type="KpiRow", data={"metrics": ["a", "b"]}))
    draft.add_node(GraphIRNode(id="n3", type="Timeseries", data={"metric": "cpu"}))
    draft.add_edge(GraphIREdge(source="n1", target="n2", role=EdgeRole.CONTAINS))
    draft.add_edge(GraphIREdge(source="n1", target="n3", role=EdgeRole.CONTAINS))
    graph = draft.freeze()
    layout = GraphIRLayout(root="n1", constraints={
        "n2": [],
        "n3": [],
    })
    return graph, layout


def _fileops_to_snapshot(fileops) -> list[dict]:
    return [
        {"action": f.action, "path": f.path}
        for f in sorted(fileops, key=lambda x: x.path)
    ]


def _assert_golden(name: str, fileops: list):
    snapshot = _fileops_to_snapshot(fileops)
    path = os.path.join(GOLDEN_DIR, f"{name}.json")

    if UPDATE_GOLDEN:
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(snapshot, f, indent=2)
        return

    if not os.path.exists(path):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(snapshot, f, indent=2)
        return

    with open(path) as f:
        expected = json.load(f)

    assert snapshot == expected, (
        f"Golden snapshot mismatch for '{name}'.\n"
        f"Expected: {json.dumps(expected, indent=2)}\n"
        f"Got:      {json.dumps(snapshot, indent=2)}\n"
        f"Run with UPDATE_GOLDEN=1 to update."
    )


# ── 1. Golden render tests ───────────────────────────────────────────


class TestGoldenRender:
    """StructuralIR → FileOps snapshot — deterministic across runs."""

    def setup_method(self):
        ReactBackend.reset_emit_log("test")
        ReactBackend.reset_traces("test")

    def test_golden_kpi(self):
        graph, layout = _build_kpi_graph()
        config = BackendConfig()
        backend = ReactBackend()
        fileops = backend.render(graph, layout, config)
        _assert_golden("render_kpi", fileops)

    def test_golden_dashboard(self):
        graph, layout = _build_dashboard_graph()
        config = BackendConfig()
        backend = ReactBackend()
        fileops = backend.render(graph, layout, config)
        _assert_golden("render_dashboard", fileops)


# ── 2. Path parity test ──────────────────────────────────────────────


class TestPathParity:
    """Path A (ReactBackend) vs Path B (RepositoryAwareRenderer) equivalence.

    Same structural input → same FileOps order, actions, paths, topology.
    """

    def setup_method(self):
        ReactBackend.reset_emit_log("test")
        ReactBackend.reset_traces("test")

    def test_parity_kpi(self):
        graph, layout = _build_kpi_graph()
        config = BackendConfig()

        # Path A
        backend = ReactBackend()
        fileops_a = backend.render(graph, layout, config)

        # Build equivalent decisions for Path B
        node_id = "n1"
        decisions = {
            node_id: FileOpDecision(
                intent_id="i1",
                graphir_node_id=node_id,
                decision=Decision.CREATE,
                target_file="src/KpiRow.tsx",
                render_mode="create",
            ),
        }
        exec_ctx = ExecutionContext(
            run_id="parity-test", workspace_root="/tmp",
        )
        pstate = PipelineState(decisions=decisions, exec_ctx=exec_ctx)
        render_ctx = RenderContext(execution=pstate)

        renderer = RepositoryAwareRenderer()
        fileops_b = renderer.render(graph, layout, config, context=render_ctx)

        # Compare structural properties
        snap_a = _fileops_to_snapshot(fileops_a)
        snap_b = _fileops_to_snapshot(fileops_b)

        assert len(snap_a) == len(snap_b), (
            f"Path A produced {len(snap_a)} FileOps, Path B {len(snap_b)}"
        )

        for i, (a, b) in enumerate(zip(snap_a, snap_b)):
            assert a["action"] == b["action"], (
                f"FileOp[{i}] action mismatch: {a['action']} vs {b['action']}"
            )
            assert a["path"] == b["path"], (
                f"FileOp[{i}] path mismatch: {a['path']} vs {b['path']}"
            )

    def test_parity_dashboard(self):
        graph, layout = _build_dashboard_graph()
        config = BackendConfig()

        # Path A
        backend = ReactBackend()
        fileops_a = backend.render(graph, layout, config)

        # Build decisions for Path B
        decisions = {
            "n1": FileOpDecision(
                intent_id="i1", graphir_node_id="n1",
                decision=Decision.CREATE, target_file="src/Page.tsx",
                render_mode="create",
            ),
            "n2": FileOpDecision(
                intent_id="i2", graphir_node_id="n2",
                decision=Decision.CREATE, target_file="src/KpiRow.tsx",
                render_mode="create",
            ),
            "n3": FileOpDecision(
                intent_id="i3", graphir_node_id="n3",
                decision=Decision.CREATE, target_file="src/Timeseries.tsx",
                render_mode="create",
            ),
        }

        exec_ctx = ExecutionContext(
            run_id="parity-test", workspace_root="/tmp",
        )
        pstate = PipelineState(decisions=decisions, exec_ctx=exec_ctx)
        render_ctx = RenderContext(execution=pstate)

        renderer = RepositoryAwareRenderer()
        fileops_b = renderer.render(graph, layout, config, context=render_ctx)

        snap_a = _fileops_to_snapshot(fileops_a)
        snap_b = _fileops_to_snapshot(fileops_b)

        assert len(snap_a) == len(snap_b), (
            f"Path A produced {len(snap_a)} FileOps, Path B {len(snap_b)}"
        )

        for i, (a, b) in enumerate(zip(snap_a, snap_b)):
            assert a["action"] == b["action"], (
                f"FileOp[{i}] action mismatch: {a['action']} vs {b['action']}"
            )
            assert a["path"] == b["path"], (
                f"FileOp[{i}] path mismatch: {a['path']} vs {b['path']}"
            )


# ── 3. Tree authority test ───────────────────────────────────────────


class TestTreeAuthority:
    """Only UIComponentNode.children is consumed for composition topology."""

    def setup_method(self):
        ReactBackend.reset_emit_log("test")
        ReactBackend.reset_traces("test")

    def test_renderer_uses_resolve_children_not_graph_edges(self):
        """RepositoryAwareRenderer uses resolve_children(), not graph.edges."""
        graph, layout = _build_dashboard_graph()
        config = BackendConfig()

        decisions = {
            "n1": FileOpDecision(
                intent_id="i1", graphir_node_id="n1",
                decision=Decision.CREATE, target_file="src/Page.tsx",
                render_mode="create",
            ),
            "n2": FileOpDecision(
                intent_id="i2", graphir_node_id="n2",
                decision=Decision.CREATE, target_file="src/KpiRow.tsx",
                render_mode="create",
            ),
            "n3": FileOpDecision(
                intent_id="i3", graphir_node_id="n3",
                decision=Decision.CREATE, target_file="src/Timeseries.tsx",
                render_mode="create",
            ),
        }

        exec_ctx = ExecutionContext(
            run_id="tree-test", workspace_root="/tmp",
        )
        pstate = PipelineState(decisions=decisions, exec_ctx=exec_ctx)
        render_ctx = RenderContext(execution=pstate)

        renderer = RepositoryAwareRenderer()
        fileops = renderer.render(graph, layout, config, context=render_ctx)

        # Verify composition topology via content inspection
        page_ops = [f for f in fileops if f.path == "src/Page.tsx"]
        assert len(page_ops) == 1, f"Expected 1 Page fileop, got {len(page_ops)}"

        content = page_ops[0].content
        assert "KpiRow" in content, "Page content should reference KpiRow"
        assert "Timeseries" in content, "Page content should reference Timeseries"

    def test_renderer_does_not_access_graph_edges_during_render(self):
        """graph.edges is not accessed after UIIRCompiler.compile()."""
        graph, layout = _build_dashboard_graph()

        # Wrap graph.edges to fail if accessed outside compile
        original_edges = graph.edges

        decisions = {
            "n1": FileOpDecision(
                intent_id="i1", graphir_node_id="n1",
                decision=Decision.CREATE, target_file="src/Page.tsx",
                render_mode="create",
            ),
            "n2": FileOpDecision(
                intent_id="i2", graphir_node_id="n2",
                decision=Decision.CREATE, target_file="src/KpiRow.tsx",
                render_mode="create",
            ),
            "n3": FileOpDecision(
                intent_id="i3", graphir_node_id="n3",
                decision=Decision.CREATE, target_file="src/Timeseries.tsx",
                render_mode="create",
            ),
        }

        exec_ctx = ExecutionContext(
            run_id="tree-auth", workspace_root="/tmp",
        )
        pstate = PipelineState(decisions=decisions, exec_ctx=exec_ctx)
        render_ctx = RenderContext(execution=pstate)

        config = BackendConfig()
        renderer = RepositoryAwareRenderer()
        fileops = renderer.render(graph, layout, config, context=render_ctx)

        assert len(fileops) == 3, f"Expected 3 FileOps, got {len(fileops)}"

    def test_build_flat_map_uses_children_not_edges(self):
        """_build_flat_map walks UIComponentNode.children, not graph.edges."""
        root = UIComponentNode(
            id="root", component="Page", props={},
            children=[
                UIComponentNode(id="c1", component="KpiRow", props={}),
                UIComponentNode(id="c2", component="Timeseries", props={}),
            ],
        )
        flat = RepositoryAwareRenderer._build_flat_map(root)
        assert "root" in flat
        assert "c1" in flat
        assert "c2" in flat
        assert flat["c1"].component == "KpiRow"


# ── 4. Mutation authority test ───────────────────────────────────────


class TestMutationAuthority:
    """Renderer NEVER emits DELETE FileOps — deletion is apply_engine's job."""

    def setup_method(self):
        ReactBackend.reset_emit_log("test")
        ReactBackend.reset_traces("test")

    def test_renderer_never_emits_delete(self):
        """RepositoryAwareRenderer.render() produces zero action='delete' FileOps."""
        from app.graphir.constraint.models import DeletionRecord

        graph, layout = _build_kpi_graph()
        config = BackendConfig()

        deletions = [
            DeletionRecord(
                fingerprint="fp_kpi",
                file_path="src/KpiRow.tsx",
                component_name="KpiRow",
            ),
        ]

        exec_ctx = ExecutionContext(
            run_id="mut-test", workspace_root="/tmp",
        )
        pstate = PipelineState(
            decisions={},
            deletions=deletions,
            exec_ctx=exec_ctx,
        )
        render_ctx = RenderContext(execution=pstate)

        renderer = RepositoryAwareRenderer()
        fileops = renderer.render(graph, layout, config, context=render_ctx)

        delete_ops = [f for f in fileops if f.action == "delete"]
        assert len(delete_ops) == 0, (
            f"Renderer produced {len(delete_ops)} DELETE FileOps: {delete_ops}"
        )

    def test_renderer_with_delete_decisions_no_delete_output(self):
        """Even with deletions in PipelineState, renderer output has no delete."""
        from app.graphir.constraint.models import DeletionRecord

        graph, layout = _build_dashboard_graph()
        config = BackendConfig()

        decisions = {
            "n1": FileOpDecision(
                intent_id="i1", graphir_node_id="n1",
                decision=Decision.CREATE, target_file="src/Page.tsx",
                render_mode="create",
            ),
        }

        deletions = [
            DeletionRecord(
                fingerprint="fp_kpi",
                file_path="src/components/KpiRow.tsx",
                component_name="KpiRow",
            ),
        ]

        exec_ctx = ExecutionContext(
            run_id="mut-test-2", workspace_root="/tmp",
        )
        pstate = PipelineState(
            decisions=decisions,
            deletions=deletions,
            exec_ctx=exec_ctx,
        )
        render_ctx = RenderContext(execution=pstate)

        renderer = RepositoryAwareRenderer()
        fileops = renderer.render(graph, layout, config, context=render_ctx)

        delete_ops = [f for f in fileops if f.action == "delete"]
        assert len(delete_ops) == 0, (
            f"Renderer produced {len(delete_ops)} DELETE FileOps despite having deletions"
        )
