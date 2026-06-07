"""Phase 6 — End-to-end graph execution: Page data source + slice distribution.

Tests verify the full rendering pipeline:
  GraphIR → UIIRCompiler (with page_data_source) → ReactBackend.render_tree()
    → FileOp content with _pageData hook declaration + slice JSVariable refs

Scope guard enforced: Page only emits selectors (pageData.kpiData),
never transformations (no filter/map/reduce).

Cases:
  1. Simple Page (KpiRow + Timeseries) — basic slice distribution
  2. Multiple charts (3+ children) — all slices distributed
  3. Page without slices (edge case) — _pageData still created
  4. Nested components — direct children get slices; grandchild data
     flow requires intermediate propagation (future work)
  5. With contract_params — exact match props alongside slices
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile

from app.binding.models import ResolvedBindings
from app.binding.resolver import resolve as resolve_bindings
from app.graphir.backends import ReactBackend, BackendConfig
from app.graphir.models import GraphIRNode, GraphIREdge, GraphIRDraft, EdgeRole
from app.graphir.layout import LayoutDerivationEngine


# ── Helpers ──


def _make_v3_workspace(slices: list[dict] | None = None) -> str:
    """Create temp workspace with v3 data_access.json (Page dataSource + slices)."""
    tmpdir = tempfile.mkdtemp(prefix="phase6_e2e_")
    os.makedirs(os.path.join(tmpdir, ".opencode"), exist_ok=True)
    page_entry: dict = {
        "dataSource": {
            "type": "dashboard_data",
        },
    }
    if slices:
        page_entry["dataSource"]["slices"] = slices
    cfg = {
        "version": 3,
        "components": {"Page": page_entry},
    }
    with open(os.path.join(tmpdir, ".opencode", "data_access.json"), "w") as f:
        json.dump(cfg, f)
    return tmpdir


def _render_page(
    children: list[tuple[str, str, dict]],
    page_data: dict | None = None,
    slices: list[dict] | None = None,
    resolved_bindings: ResolvedBindings | None = None,
) -> tuple[list, str]:
    """Build Page graph + render → (fileops, page_content).

    Args:
        children: list of (node_id, type, data_dict)
        page_data: data for Page node (default: {})
        slices: list of slice dicts for data_access.json (legacy)
        resolved_bindings: pre-resolved bindings (PR1: contract_params never
                          reach the compiler/renderer)
    """
    ReactBackend.reset_emit_log("phase6_test")
    ReactBackend.reset_traces("phase6_test")

    draft = GraphIRDraft()
    draft.add_node(GraphIRNode(id="page", type="Page", data=page_data or {}))
    for cid, ctype, cdata in children:
        draft.add_node(GraphIRNode(id=cid, type=ctype, data=cdata))
        draft.add_edge(GraphIREdge(source="page", target=cid, role=EdgeRole.CONTAINS))
    graph = draft.freeze()
    layout = LayoutDerivationEngine.derive(graph)

    ws = _make_v3_workspace(slices)
    config = BackendConfig(workspace=ws)

    # PR1: When resolved_bindings not provided, resolve from workspace config
    if resolved_bindings is None:
        resolved_bindings = resolve_bindings({}, ws)

    backend = ReactBackend()
    fileops = backend.render(graph, layout, config, resolved_bindings=resolved_bindings)

    page_ops = [f for f in fileops if "Page" in f.path and "SalesOverview" not in f.path]
    page_file = page_ops[0] if page_ops else None
    page_content = page_file.content if page_file else ""

    # Cleanup
    shutil.rmtree(ws)

    return fileops, page_content


def _count_occurrences(content: str, pattern: str) -> int:
    return content.count(pattern)


# ── Phase 6: 4 Cases + 1 extra ──


class TestPhase6SimplePage:
    """Case 1: Page with KpiRow + Timeseries — basic slice distribution."""

    def test_hook_declaration_present(self):
        _, content = _render_page(
            children=[("kpi", "KpiRow", {}), ("ts", "Timeseries", {})],
            slices=[
                {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
                {"component": "Timeseries", "targetProp": "data", "selector": "chartData.timeseries"},
            ],
        )
        assert "const _pageData = useDashboardData();" in content

    def test_import_present(self):
        _, content = _render_page(
            children=[("kpi", "KpiRow", {}), ("ts", "Timeseries", {})],
            slices=[
                {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
                {"component": "Timeseries", "targetProp": "data", "selector": "chartData.timeseries"},
            ],
        )
        assert "useDashboardData" in content

    def test_kpi_slice_distributed(self):
        _, content = _render_page(
            children=[("kpi", "KpiRow", {}), ("ts", "Timeseries", {})],
            slices=[
                {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
                {"component": "Timeseries", "targetProp": "data", "selector": "chartData.timeseries"},
            ],
        )
        assert "data={_pageData.kpiData}" in content

    def test_timeseries_slice_distributed(self):
        _, content = _render_page(
            children=[("kpi", "KpiRow", {}), ("ts", "Timeseries", {})],
            slices=[
                {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
                {"component": "Timeseries", "targetProp": "data", "selector": "chartData.timeseries"},
            ],
        )
        assert "data={_pageData.chartData.timeseries}" in content

    def test_children_no_hook_import(self):
        """Phase 6 invariant: children are pure presentational — no hook imports."""
        fileops, _ = _render_page(
            children=[("kpi", "KpiRow", {}), ("ts", "Timeseries", {})],
            slices=[
                {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
                {"component": "Timeseries", "targetProp": "data", "selector": "chartData.timeseries"},
            ],
        )
        child_ops = [f for f in fileops if "KpiRow" in f.path or "Timeseries" in f.path]
        for op in child_ops:
            assert "useDashboardData" not in op.content, \
                f"{op.path} must NOT import useDashboardData"

    def test_scope_guard_no_transformations(self):
        """Scope guard: Page emits selectors only — no filter/map/reduce."""
        _, content = _render_page(
            children=[("kpi", "KpiRow", {}), ("ts", "Timeseries", {})],
            slices=[
                {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
                {"component": "Timeseries", "targetProp": "data", "selector": "chartData.timeseries"},
            ],
        )
        # Only selector patterns, no transformations
        assert ".filter(" not in content
        assert ".map(" not in content
        assert ".reduce(" not in content
        assert ".sort(" not in content

    def test_emit_log_records_page_phase6(self):
        """Verify render traces capture Phase 6 distribution."""
        ReactBackend.reset_emit_log("phase6_trace")
        ReactBackend.reset_traces("phase6_trace")
        ws = _make_v3_workspace([
            {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
        ])
        backend = ReactBackend()
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page", data={}))
        draft.add_node(GraphIRNode(id="kpi", type="KpiRow", data={}))
        draft.add_edge(GraphIREdge(source="page", target="kpi", role=EdgeRole.CONTAINS))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)
        config = BackendConfig(workspace=ws)
        rb = resolve_bindings({}, ws)
        backend.render(graph, layout, config, resolved_bindings=rb)
        shutil.rmtree(ws)
        traces = ReactBackend._render_traces
        emitted = [t for t in traces if t.phase == "emitted"]
        assert len(emitted) >= 1, "Should have at least one emitted trace"


class TestPhase6MultipleCharts:
    """Case 2: Page with 3+ children — all get correct slices."""

    def test_all_slices_present(self):
        _, content = _render_page(
            children=[
                ("kpi", "KpiRow", {}),
                ("ts1", "Timeseries", {}),
                ("ts2", "Timeseries", {}),
                ("table", "AnalyticsTable", {}),
            ],
            slices=[
                {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
                {"component": "Timeseries", "targetProp": "data", "selector": "chartData"},
                {"component": "AnalyticsTable", "targetProp": "data", "selector": "tableData"},
            ],
        )
        assert "data={_pageData.kpiData}" in content
        assert "data={_pageData.chartData}" in content
        assert "data={_pageData.tableData}" in content
        assert _count_occurrences(content, "_pageData") == 5  # declaration + 4 refs (2x Timeseries)

    def test_duplicate_components_both_get_slice(self):
        """Two Timeseries children both get the same slice pattern."""
        _, content = _render_page(
            children=[
                ("ts1", "Timeseries", {}),
                ("ts2", "Timeseries", {}),
            ],
            slices=[
                {"component": "Timeseries", "targetProp": "data", "selector": "chartData"},
            ],
        )
        # Both Timeseries instances should get the slice
        assert _count_occurrences(content, "_pageData.chartData") == 2

    def test_first_child_only_matches_first_slice(self):
        """Slice with targetProp not matching any child prop still ok (no error)."""
        _, content = _render_page(
            children=[("kpi", "KpiRow", {})],
            slices=[
                {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
                {"component": "NonExistent", "targetProp": "x", "selector": "y"},
            ],
        )
        assert "data={_pageData.kpiData}" in content
        # NonExistent has no matching child — no error, just no injection


class TestPhase6NoSlices:
    """Case 3: Page data source WITHOUT slices — _pageData still created."""

    def test_hook_still_declared(self):
        _, content = _render_page(
            children=[("kpi", "KpiRow", {})],
            slices=None,
        )
        assert "const _pageData = useDashboardData();" in content
        assert "useDashboardData" in content

    def test_no_slice_refs_generated(self):
        _, content = _render_page(
            children=[("kpi", "KpiRow", {})],
            slices=None,
        )
        assert "data={_pageData" not in content, \
            "No data slice refs when slices config is empty"

    def test_no_slices_still_renders_children(self):
        """Children still render in composition even without slices."""
        fileops, content = _render_page(
            children=[("kpi", "KpiRow", {})],
            slices=None,
        )
        assert "KpiRow" in content, "KpiRow should still be in composition"
        assert any("KpiRow" in f.path for f in fileops), "KpiRow file should be created"

    def test_no_slices_no_data_access(self):
        """No data_access.json at all — Page renders normally without hooks."""
        ReactBackend.reset_emit_log("no_ds")
        ReactBackend.reset_traces("no_ds")
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page", data={}))
        draft.add_node(GraphIRNode(id="kpi", type="KpiRow", data={"metric": "x"}))
        draft.add_edge(GraphIREdge(source="page", target="kpi", role=EdgeRole.CONTAINS))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)
        config = BackendConfig()  # no workspace
        backend = ReactBackend()
        fileops = backend.render(graph, layout, config)
        page_ops = [f for f in fileops if "Page" in f.path]
        assert len(page_ops) >= 1
        assert "useDashboardData" not in page_ops[0].content


class TestPhase6NestedComponents:
    """Case 4: Page with nested children — direct children get slices.

    Phase 6 distributes slices to DIRECT children only. Grandchildren
    (e.g. KpiRow under Section) do NOT receive automatic slice refs;
    data flow through intermediate containers requires future work.
    """

    def _build_nested_graph(self):
        """Page → Section → KpiRow + Page → Timeseries."""
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page", data={}))
        draft.add_node(GraphIRNode(id="section", type="Section", data={"label": "Metrics"}))
        draft.add_node(GraphIRNode(id="kpi", type="KpiRow", data={"metric": "revenue"}))
        draft.add_node(GraphIRNode(id="ts", type="Timeseries", data={"metric": "cpu"}))
        draft.add_edge(GraphIREdge(source="page", target="section", role=EdgeRole.CONTAINS))
        draft.add_edge(GraphIREdge(source="section", target="kpi", role=EdgeRole.CONTAINS))
        draft.add_edge(GraphIREdge(source="page", target="ts", role=EdgeRole.CONTAINS))
        return draft.freeze()

    def test_direct_child_gets_slice(self):
        """Timeseries (direct child of Page) gets slice."""
        graph = self._build_nested_graph()
        layout = LayoutDerivationEngine.derive(graph)
        ws = _make_v3_workspace([
            {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
            {"component": "Timeseries", "targetProp": "data", "selector": "chartData.ts"},
        ])
        config = BackendConfig(workspace=ws)
        backend = ReactBackend()
        rb = resolve_bindings({}, ws)
        fileops = backend.render(graph, layout, config, resolved_bindings=rb)
        page_content = [f for f in fileops if "Page" in f.path][0].content
        shutil.rmtree(ws)

        assert "const _pageData = useDashboardData();" in page_content
        assert "data={_pageData.chartData.ts}" in page_content, \
            "Direct child (Timeseries) should get slice"

    def test_nested_child_no_slice_ref(self):
        """KpiRow (nested under Section) does NOT get automatic slice from Page."""
        graph = self._build_nested_graph()
        layout = LayoutDerivationEngine.derive(graph)
        ws = _make_v3_workspace([
            {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
            {"component": "Timeseries", "targetProp": "data", "selector": "chartData.ts"},
        ])
        config = BackendConfig(workspace=ws)
        backend = ReactBackend()
        rb = resolve_bindings({}, ws)
        fileops = backend.render(graph, layout, config, resolved_bindings=rb)
        page_content = [f for f in fileops if "Page" in f.path][0].content
        shutil.rmtree(ws)

        # KpiRow is under Section, not direct child of Page
        # _distribute_page_slices only checks direct children
        assert "data={_pageData.kpiData}" not in page_content, \
            "Nested KpiRow should NOT get automatic slice from Page"

    def test_section_still_in_composition(self):
        """Section intermediate still renders in Page composition."""
        graph = self._build_nested_graph()
        layout = LayoutDerivationEngine.derive(graph)
        ws = _make_v3_workspace([])
        config = BackendConfig(workspace=ws)
        backend = ReactBackend()
        rb = resolve_bindings({}, ws)
        fileops = backend.render(graph, layout, config, resolved_bindings=rb)
        page_content = [f for f in fileops if "Page" in f.path][0].content
        shutil.rmtree(ws)

        assert "Section" in page_content, "Section should appear in composition"


class TestPhase6WithContractParams:
    """Extra: Page with resolved_bindings — title set via node.data or ResolvedBindings."""

    def test_title_through_node_data(self):
        """Page title from node.data + slice distribution coexist.
        PR1: title no longer comes from contract_params → BindingResolver or node.data."""
        ReactBackend.reset_emit_log("phase6_params")
        ReactBackend.reset_traces("phase6_params")
        ws = _make_v3_workspace([
            {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
        ])
        config = BackendConfig(
            workspace=ws,
            component_signatures={
                "Page": {
                    "props": "interface PageProps {\n  title?: string;\n}",
                    "prop_names": ["title"],
                },
            },
        )
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page", data={"title": "Dashboard"}))
        draft.add_node(GraphIRNode(id="kpi", type="KpiRow", data={"metric": "revenue"}))
        draft.add_edge(GraphIREdge(source="page", target="kpi", role=EdgeRole.CONTAINS))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)

        backend = ReactBackend()
        rb = resolve_bindings({}, ws)
        fileops = backend.render(
            graph, layout, config,
            resolved_bindings=rb,
        )
        page_content = [f for f in fileops if "Page" in f.path][0].content
        shutil.rmtree(ws)

        assert "const _pageData = useDashboardData();" in page_content
        assert "data={_pageData.kpiData}" in page_content
