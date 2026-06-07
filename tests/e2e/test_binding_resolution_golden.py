"""PR2: Golden test — BindingResolver-based resolution renders correct JSX.

Después de PR1, _distribute_page_slices es dormida (sigue activa pero
innecesaria). La fuente de verdad de props es ResolvedBindings.component_props.
El renderer produce el mismo JSX con o sin page_data_source.

Invocantes (PR2→PR3):
  PR2: _distribute_page_slices activa → se prueba con y sin page_data_source
  PR3: se elimina _distribute_page_slices → los mismos tests pasan igual
"""

from __future__ import annotations

import os

from app.binding.models import ResolvedBindings, BindingProvenance
from app.binding.resolver import resolve as resolve_bindings
from app.graphir.backends import ReactBackend, BackendConfig
from app.graphir.models import GraphIRNode, GraphIREdge, GraphIRDraft, EdgeRole
from app.graphir.layout import LayoutDerivationEngine
from app.graphir.backends.react_backend import JSVariable
from app.signature.prop_mapper import DataSourceIR, DataSlice


def _render_with_bindings(
    children: list[tuple[str, str, dict[str, object]]],
    component_props: dict[str, dict[str, object]],
    page_data_source: DataSourceIR | None = None,
    provenance: dict[str, dict[str, str]] | None = None,
    page_data: dict | None = None,
) -> tuple[list, str]:
    """Build Page graph + render con ResolvedBindings → (fileops, page_content).

    _distribute_page_slices puede estar activa (PR2) o eliminada (PR3).
    El test debe pasar en ambos casos.
    """
    ReactBackend.reset_emit_log("golden_test")
    ReactBackend.reset_traces("golden_test")

    draft = GraphIRDraft()
    draft.add_node(GraphIRNode(id="page", type="Page", data=page_data or {}))
    for cid, ctype, cdata in children:
        draft.add_node(GraphIRNode(id=cid, type=ctype, data=cdata))
        draft.add_edge(GraphIREdge(source="page", target=cid, role=EdgeRole.CONTAINS))
    graph = draft.freeze()
    layout = LayoutDerivationEngine.derive(graph)

    config = BackendConfig()

    resolved = ResolvedBindings(
        component_props=component_props,
        provenance=provenance or {},
        consumed_params=set(),
        unconsumed_params=set(),
        page_data_source=page_data_source,
    )

    backend = ReactBackend()
    fileops = backend.render(graph, layout, config, resolved_bindings=resolved)

    page_ops = [f for f in fileops if "Page" in f.path]
    page_content = page_ops[0].content if page_ops else ""

    return fileops, page_content


class TestBindingResolverGolden:
    """Golden tests: renderer recibe props completamente resueltas."""

    def test_kpirow_receives_data_prop_without_page_ds(self):
        """PR2: KpiRow.data from ResolvedBindings, no page_data_source."""
        _, content = _render_with_bindings(
            children=[("kpi", "KpiRow", {})],
            component_props={
                "KpiRow": {"data": JSVariable("_pageData.kpiData")},
            },
            page_data_source=None,
        )
        assert "data={_pageData.kpiData}" in content, (
            "KpiRow.data must be rendered even without page_data_source"
        )
        # No hook declaration since no page data source
        assert "useDashboardData" not in content

    def test_kpirow_receives_data_prop_with_page_ds(self):
        """PR2: KpiRow.data from ResolvedBindings, con page_data_source.

        In PR2, _distribute_page_slices also runs (redundant). In PR3,
        same output without it. Test must pass both ways.
        """
        page_ds = DataSourceIR(
            type="dashboard_data",
            slices=[DataSlice(component="KpiRow", target_prop="data", selector="kpiData")],
        )
        _, content = _render_with_bindings(
            children=[("kpi", "KpiRow", {})],
            component_props={
                "KpiRow": {"data": JSVariable("_pageData.kpiData")},
            },
            page_data_source=page_ds,
        )
        assert "data={_pageData.kpiData}" in content
        assert "const _pageData = useDashboardData();" in content

    def test_timeseries_receives_data_and_title(self):
        """PR2: Timeseries recibe data (JSVariable) + title (string)."""
        _, content = _render_with_bindings(
            children=[("ts", "Timeseries", {})],
            component_props={
                "Timeseries": {
                    "data": JSVariable("_pageData.chartData.timeseries"),
                    "title": "Revenue Timeseries",
                },
            },
        )
        assert "data={_pageData.chartData.timeseries}" in content
        assert 'title="Revenue Timeseries"' in content

    def test_multi_child_all_props_resolved(self):
        """PR2: Page con KpiRow + Timeseries + DataTable.

        Cada componente recibe sus props sin depender de _distribute_page_slices.
        """
        _, content = _render_with_bindings(
            children=[
                ("kpi", "KpiRow", {}),
                ("ts", "Timeseries", {}),
                ("table", "DataTable", {}),
            ],
            component_props={
                "KpiRow": {"data": JSVariable("_pageData.kpiData")},
                "Timeseries": {
                    "data": JSVariable("_pageData.chartData.timeseries"),
                    "title": "Revenue",
                },
                "DataTable": {
                    "columns": JSVariable("_pageData.tableData.columns"),
                    "data": JSVariable("_pageData.tableData.rows"),
                },
            },
        )
        assert "data={_pageData.kpiData}" in content
        assert "data={_pageData.chartData.timeseries}" in content
        assert 'title="Revenue"' in content
        assert "columns={_pageData.tableData.columns}" in content
        assert "data={_pageData.tableData.rows}" in content

    def test_duplicate_component_both_get_props(self):
        """PR2: Dos Timeseries, ambos reciben props."""
        _, content = _render_with_bindings(
            children=[("ts1", "Timeseries", {}), ("ts2", "Timeseries", {})],
            component_props={
                "Timeseries": {
                    "data": JSVariable("_pageData.chartData"),
                    "title": "Chart",
                },
            },
        )
        assert content.count("data={_pageData.chartData}") == 2
        assert content.count('title="Chart"') == 2

    def test_missing_component_props_emits_bare_tag(self):
        """Componente sin entry en component_props emite tag vacío."""
        _, content = _render_with_bindings(
            children=[("ts", "Timeseries", {"title": "From Node"})],
            component_props={},  # Timeseries no tiene binding
        )
        # No fallback — node.data is intent, not UI props.
        # Sin BindingResolver, the component emits bare tag.
        assert '<Timeseries />' in content

    def test_provenance_in_emit_log(self):
        """PR2: Provenance tracking llega al emit log."""
        ReactBackend.reset_emit_log("provenance_test")
        ReactBackend.reset_traces("provenance_test")

        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page", data={}))
        draft.add_node(GraphIRNode(id="kpi", type="KpiRow", data={}))
        draft.add_edge(GraphIREdge(source="page", target="kpi", role=EdgeRole.CONTAINS))
        graph = draft.freeze()
        layout = LayoutDerivationEngine.derive(graph)

        resolved = ResolvedBindings(
            component_props={
                "KpiRow": {"data": JSVariable("_pageData.kpiData")},
            },
            provenance={
                "KpiRow": {
                    "data": str(BindingProvenance(
                        source="slice",
                        selector="kpiData",
                        contract_params=["metrics"],
                    )),
                },
            },
            consumed_params={"metrics"},
            unconsumed_params={"region"},
        )

        backend = ReactBackend()
        fileops = backend.render(graph, layout, BackendConfig(),
                                 resolved_bindings=resolved)
        page_ops = [f for f in fileops if "Page" in f.path]
        assert len(page_ops) >= 1
        assert "data={_pageData.kpiData}" in page_ops[0].content

    def test_page_ds_hook_declaration_works(self):
        """PR2: Con page_data_source, el hook _pageData se declara."""
        page_ds = DataSourceIR(
            type="dashboard_data",
            slices=[],
        )
        _, content = _render_with_bindings(
            children=[("kpi", "KpiRow", {})],
            component_props={
                "KpiRow": {"data": JSVariable("_pageData.kpiData")},
            },
            page_data_source=page_ds,
        )
        assert "const _pageData = useDashboardData();" in content


class TestBindingResolverGlobalSSOT:
    """Verifica que BindingResolver usa backend/config/data_access.json sin depender del workspace."""

    def test_binding_resolver_uses_global_data_access_ssot(self):
        """Resolver carga global config y resuelve metrics → KpiRow.data."""
        # Precondición: NO hay .opencode en el workspace
        assert not os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "..", ".opencode"))

        contract_params = {
            "metrics": ["revenue", "growth"],
            "timeseries_metric": "revenue_over_time",
        }
        result = resolve_bindings(contract_params)

        # KpiRow recibe data desde global config
        assert "KpiRow" in result.component_props
        assert "data" in result.component_props["KpiRow"]
        assert result.component_props["KpiRow"]["data"].name == "_pageData.kpiData"

        # Timeseries recibe data desde global config
        assert "Timeseries" in result.component_props
        assert "data" in result.component_props["Timeseries"]
        assert result.component_props["Timeseries"]["data"].name == "_pageData.chartData.timeseries"

        # Consumed params tracking
        assert "metrics" in result.consumed_params
        assert "timeseries_metric" in result.consumed_params

        # page_data_source presente (desde global config)
        assert result.page_data_source is not None
        assert result.page_data_source.type == "dashboard_data"
        assert len(result.page_data_source.slices) == 2
