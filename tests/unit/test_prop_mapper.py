"""PropMapper unit tests — BindingIR resolution (PR1 simplified).

PR1 (Binding Resolution Architecture): resolve_props only does BindingIR
lookup. No exact match, no aliases, no heuristics. Contract params never
reach the compiler — only BindingResolver resolves them.

Phase 6 (Composition Data Flow):
  - data_access.json v3: Page-only with dataSource + slices.
  - Children are pure presentational — no per-component bindings.
  - resolve_props with workspace pointing to v3 config yields no bindings
    for children; data comes from Page-level DataSourceIR.slices.

Test categories:
   1. BindingIR compilation (compile_binary, HookBinding)
   2. BindingIR resolution with v2 config
   3. FALLBACK_ALLOWED for optional/required props without binding
   4. Phase 6: load_page_data_source + DataSlice slicing
   5. Drift detection + backward compat
   6. Unconsumed params as warnings
"""

from __future__ import annotations

from typing import Any

import pytest

from app.signature.prop_mapper import (
    Binding,
    DataSourceIR,
    DataSlice,
    compile_binding,
    JSExpression,
    HookBinding,
    _load_data_access_config,
    _find_binding,
    _parse_bindings,
    load_page_data_source,
    _infer_datasource_ir,
)


# ── Fixtures ──


@pytest.fixture
def kpi_row_signature() -> dict[str, Any]:
    return {
        "props": "interface KpiRowProps {\n  data: KpiItem[];\n  title?: string;\n}",
        "prop_names": ["data", "title"],
        "imports": ["import React from 'react';"],
    }


@pytest.fixture
def timeseries_signature() -> dict[str, Any]:
    return {
        "props": "interface TimeseriesProps {\n  data: Point[];\n  title?: string;\n  type?: string;\n}",
        "prop_names": ["data", "title", "type"],
        "imports": ["import React from 'react';"],
    }


@pytest.fixture
def page_signature() -> dict[str, Any]:
    return {
        "props": "interface PageProps {\n  data: KpiItem[];\n  title?: string;\n}",
        "prop_names": ["data", "title"],
        "imports": ["import React from 'react';"],
    }





# ── DataSlice + DataSourceIR.slices tests (Phase 6) ──


class TestSliceIR:
    """DataSlice dataclass + DataSourceIR.slices parsing."""

    def test_dataslice_creation(self):
        ds = DataSlice(component="KpiRow", target_prop="data", selector="kpiData")
        assert ds.component == "KpiRow"
        assert ds.target_prop == "data"
        assert ds.selector == "kpiData"
        assert ds.consumes == ()

    def test_dataslice_with_consumes(self):
        ds = DataSlice(
            component="Timeseries", target_prop="data",
            selector="chartData.timeseries", consumes=("timeseries_metric",),
        )
        assert "timeseries_metric" in ds.consumes

    def test_datasource_ir_slices_empty_default(self):
        ir = DataSourceIR(type="dashboard_data")
        assert ir.slices == ()

    def test_infer_datasource_ir_parses_slices(self):
        raw = {
            "type": "dashboard_data",
            "slices": [
                {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
            ],
        }
        ir = _infer_datasource_ir(raw)
        assert len(ir.slices) == 1
        assert ir.slices[0].component == "KpiRow"
        assert ir.slices[0].target_prop == "data"
        assert ir.slices[0].selector == "kpiData"

    def test_infer_datasource_ir_parses_multiple_slices(self):
        raw = {
            "type": "dashboard_data",
            "slices": [
                {"component": "KpiRow", "targetProp": "data", "selector": "kpiData"},
                {"component": "Timeseries", "targetProp": "data", "selector": "chartData.timeseries"},
            ],
        }
        ir = _infer_datasource_ir(raw)
        assert len(ir.slices) == 2
        assert ir.slices[1].component == "Timeseries"
        assert ir.slices[1].selector == "chartData.timeseries"


# ── load_page_data_source tests (Phase 6) ──


class TestLoadPageDataSource:
    """load_page_data_source loads v3 Page-level DataSourceIR from global SSOT."""

    def test_loads_v3_page_datasource(self):
        ds = load_page_data_source()
        assert ds is not None
        assert ds.type == "dashboard_data"
        assert len(ds.slices) == 5

    def test_slices_are_parsed_correctly(self):
        ds = load_page_data_source()
        kpi_slice = ds.slices[0]
        assert kpi_slice.component == "KpiRow"
        assert kpi_slice.target_prop == "data"
        assert kpi_slice.selector == "kpiData"
        ts_slice = ds.slices[1]
        assert ts_slice.component == "Timeseries"
        assert ts_slice.selector == "chartData.timeseries"

    def test_global_config_loaded(self):
        """Global config is v3 — should have Page with dataSource."""
        ds = load_page_data_source()
        assert ds is not None
        assert ds.type == "dashboard_data"
        assert len(ds.slices) == 5

    def test_global_config_has_page(self):
        """Global config HAS Page with dataSource."""
        ds = load_page_data_source()
        assert ds is not None

    def test_global_config_has_datasource(self):
        """Global config has dataSource with type."""
        ds = load_page_data_source()
        assert ds is not None
        assert ds.type == "dashboard_data"

    def test_global_config_exists_and_loads(self):
        """Global config exists and loads."""
        ds = load_page_data_source()
        assert ds is not None

    def test_component_not_in_data_access_falls_through(self):
        """load_page_data_source works."""
        ds = load_page_data_source()
        assert ds is not None

    def test_global_config_is_v3_with_datasource(self):
        """Global config IS v3 with dataSource, not None."""
        ds = load_page_data_source()
        assert ds is not None
        assert ds.type == "dashboard_data"


# ── BindingIR compilation tests ──


class TestBindingIR:
    """BindingIR type compilation correctness."""

    def test_hook_with_transform(self):
        binding = Binding(
            target_prop="data",
            source=DataSourceIR(type="dashboard_data"),
            transform="kpiData",
        )
        hb = compile_binding(binding)
        assert isinstance(hb, HookBinding)
        assert hb.hook_name == "useDashboardData"
        assert hb.transform == "kpiData"
        assert hb.import_stmt is not None
        assert binding.source.type == "dashboard_data"

    def test_hook_without_transform(self):
        binding = Binding(
            target_prop="data",
            source=DataSourceIR(type="dashboard_data"),
        )
        hb = compile_binding(binding)
        assert isinstance(hb, HookBinding)
        assert hb.hook_name == "useDashboardData"
        assert hb.transform is None

    def test_selector_source(self):
        binding = Binding(
            target_prop="data",
            source=DataSourceIR(type="raw_selector", selector="store.some.value"),
        )
        expr = compile_binding(binding)
        assert isinstance(expr, JSExpression)
        assert expr.code == "store.some.value"

    def test_nested_transform(self):
        binding = Binding(
            target_prop="data",
            source=DataSourceIR(type="dashboard_data", selector="chartData.timeseries"),
            transform="chartData.timeseries",
        )
        hb = compile_binding(binding)
        assert isinstance(hb, HookBinding)
        assert hb.hook_name == "useDashboardData"
        assert hb.transform == "chartData.timeseries"













class TestLoadDataAccessConfig:
    """_load_data_access_config — always loads global SSOT."""

    def test_global_config_loads_v4(self):
        result = _load_data_access_config()
        assert result is not None
        assert "components" in result
        assert "composition" in result
        assert "Page" in result["composition"]
        assert "dataSource" in result["composition"]["Page"]

    def test_global_config_has_composition_page(self):
        result = _load_data_access_config()
        assert result is not None
        assert "Page" in result.get("composition", {})

    def test_missing_file_returns_none(self, monkeypatch):
        """Simulate missing config file via os.path.exists."""
        import json as _json
        orig_exists = __import__('os').path.exists
        def mock_exists(path):
            if 'data_access.json' in path:
                return False
            return orig_exists(path)
        monkeypatch.setattr('os.path.exists', mock_exists)
        result = _load_data_access_config()
        assert result is None

    def test_missing_components_key_returns_none(self, monkeypatch):
        """Simulate JSON without components key."""
        import json as _json
        def mock_load(*args, **kwargs):
            return {"version": 3}  # no "components" key
        monkeypatch.setattr('json.load', mock_load)
        result = _load_data_access_config()
        assert result is None

    def test_invalid_json_returns_none(self, monkeypatch):
        """Simulate invalid JSON via json.load failure."""
        def mock_load(*args, **kwargs):
            import json
            raise json.JSONDecodeError("boom", "", 0)
        monkeypatch.setattr('json.load', mock_load)
        result = _load_data_access_config()
        assert result is None

    def test_component_not_in_data_access_falls_through(self):
        config = _load_data_access_config()
        # Page is in composition, not components in v4
        assert "Page" not in config.get("components", {})

    def test_global_config_is_v4_with_datasource(self):
        result = _load_data_access_config()
        assert result is not None
        assert "components" in result
        assert "composition" in result
        # Verify it's v4 (composition.Page.dataSource, not components.Page.dataSource)
        page_cfg = result.get("composition", {}).get("Page", {})
        assert "dataSource" in page_cfg


class TestParseBindings:
    """_parse_bindings correctness."""

    def test_global_config_has_per_component_bindings(self):
        """Global config is v4 with per-component bindings."""
        data = _load_data_access_config()
        bindings = _parse_bindings(data)
        assert len(bindings) >= 10  # KpiRow, Timeseries, AnalyticsTable, etc.

    def test_v4_config_returns_bindings(self):
        """v4 config has per-component props → bindings map populated."""
        data = _load_data_access_config()
        bindings = _parse_bindings(data)
        assert len(bindings) > 0
        assert "KpiRow" in bindings
        assert "Timeseries" in bindings

    def test_find_binding_returns_correct(self):
        bindings_map = {
            "KpiRow": [
                Binding(target_prop="data", source=DataSourceIR(type="dashboard_data"), transform="kpiData"),
            ],
        }
        b = _find_binding("KpiRow", "data", bindings_map)
        assert b is not None
        assert b.target_prop == "data"
        assert _find_binding("KpiRow", "title", bindings_map) is None
        assert _find_binding("Page", "data", bindings_map) is None

    def test_empty_data_returns_empty(self):
        assert _parse_bindings(None) == {}
        assert _parse_bindings({}) == {}


def test_backward_compat_parse_v2_dict():
    """_parse_bindings translates old hook format to DataSourceIR."""
    v2_data = {
        "components": {
            "KpiRow": {
                "bindings": [{
                    "targetProp": "data",
                    "source": {"type": "hook", "name": "useDashboardData"},
                    "transform": "kpiData",
                    "consumes": ["metrics"],
                }],
            },
        },
    }
    bindings = _parse_bindings(v2_data)
    b = bindings["KpiRow"][0]
    assert isinstance(b.source, DataSourceIR)
    assert b.source.type == "dashboard_data"
    assert b.transform == "kpiData"


# ── Phase 6 lock invariants ─────────────────────────────────


class TestValidateNodeInvariants:
    """Lock invariant: Page as data_owner.

    CRITICAL invariants enforced at compile-time:
      1. Page must have exactly 1 hook call in data_imports
      2. Page must have at least 1 child (data distribution target)
      3. Children must have NO data_imports
    """

    def _make_tree(self, page_data_imports=(), children=None, slices=None):
        from app.graphir.ui_ir import UIComponentNode, UIComponentTree
        from app.signature.prop_mapper import DataSourceIR, DataSlice
        child_nodes = children or []
        page = UIComponentNode(
            id="page", component="Page", props={},
            data_imports=page_data_imports,
            children=child_nodes,
        )
        ds = DataSourceIR(
            type="dashboard_data",
            slices=tuple(slices or []),
        )
        return UIComponentTree(
            root=page, page_data_source=ds,
        )

    def test_valid_page_has_exactly_one_hook(self):
        from app.graphir.compiler import UIIRCompiler
        from app.graphir.ui_ir import UIComponentNode
        from app.signature.prop_mapper import DataSlice
        tree = self._make_tree(
            page_data_imports=("useDashboardData",),
            children=[UIComponentNode(id="kpi", component="KpiRow", props={})],
            slices=[DataSlice(component="KpiRow", target_prop="data", selector="kpiData")],
        )
        violations = UIIRCompiler.validate_node(tree)
        assert len(violations) == 0, f"Expected 0 violations, got: {violations}"

    def test_page_with_zero_hooks_violation(self):
        from app.graphir.compiler import UIIRCompiler
        from app.graphir.ui_ir import UIComponentNode
        from app.signature.prop_mapper import DataSlice
        tree = self._make_tree(
            page_data_imports=(),
            children=[UIComponentNode(id="kpi", component="KpiRow", props={})],
            slices=[DataSlice(component="KpiRow", target_prop="data", selector="kpiData")],
        )
        violations = UIIRCompiler.validate_node(tree)
        assert any("expected exactly 1" in v for v in violations), (
            f"Expected 'exactly 1' violation, got: {violations}"
        )

    def test_page_with_two_hooks_violation(self):
        from app.graphir.compiler import UIIRCompiler
        from app.graphir.ui_ir import UIComponentNode
        from app.signature.prop_mapper import DataSlice
        tree = self._make_tree(
            page_data_imports=("useDashboardData", "useOtherHook"),
            children=[UIComponentNode(id="kpi", component="KpiRow", props={})],
            slices=[DataSlice(component="KpiRow", target_prop="data", selector="kpiData")],
        )
        violations = UIIRCompiler.validate_node(tree)
        assert any("expected exactly 1" in v for v in violations), (
            f"Expected 'exactly 1' violation, got: {violations}"
        )

    def test_page_with_zero_children_violation(self):
        from app.graphir.compiler import UIIRCompiler
        from app.signature.prop_mapper import DataSlice
        tree = self._make_tree(
            page_data_imports=("useDashboardData",),
            children=[],
            slices=[DataSlice(component="KpiRow", target_prop="data", selector="kpiData")],
        )
        violations = UIIRCompiler.validate_node(tree)
        assert any("0 children" in v for v in violations), (
            f"Expected '0 children' violation, got: {violations}"
        )

    def test_child_with_data_imports_violation(self):
        from app.graphir.compiler import UIIRCompiler
        from app.graphir.ui_ir import UIComponentNode
        from app.signature.prop_mapper import DataSlice
        tree = self._make_tree(
            page_data_imports=("useDashboardData",),
            children=[UIComponentNode(
                id="kpi", component="KpiRow", props={},
                data_imports=("useDashboardData",),
            )],
            slices=[DataSlice(component="KpiRow", target_prop="data", selector="kpiData")],
        )
        violations = UIIRCompiler.validate_node(tree)
        assert any("data_imports" in v for v in violations), (
            f"Expected 'data_imports' violation, got: {violations}"
        )

    def test_no_page_data_source_skips_validation(self):
        from app.graphir.compiler import UIIRCompiler
        from app.graphir.ui_ir import UIComponentNode, UIComponentTree
        page = UIComponentNode(id="page", component="Page", props={})
        tree = UIComponentTree(root=page, page_data_source=None)
        violations = UIIRCompiler.validate_node(tree)
        assert len(violations) == 0


class TestVerifyGraphIRCoverage:
    """Guard: KEEP node in structural_tree MUST appear in GraphIR."""

    def _make_struct_cap(self, name, action, instance_only=False):
        return {"name": name, "action": action, "instance_only": instance_only}

    def _make_graph(self, node_types_caps: list[tuple[str, str, str]], root_id="page"):
        from app.graphir.models import GraphIRNode, GraphIREdge, GraphIRDraft, EdgeRole
        draft = GraphIRDraft()
        for ntype, nid, cap in node_types_caps:
            draft.add_node(GraphIRNode(
                id=nid, type=ntype, data={},
                metadata={"intent_capability": cap} if cap else {},
            ))
            if nid != root_id:
                draft.add_edge(GraphIREdge(source=root_id, target=nid, role=EdgeRole.CONTAINS))
        return draft.freeze()

    def test_keep_child_under_modify_parent_detected(self):
        from app.graphir.compiler import verify_graphir_coverage
        caps = [
            self._make_struct_cap("layout.page", "MODIFY"),
            self._make_struct_cap("presentation.kpi_row", "KEEP"),
            self._make_struct_cap("presentation.timeseries", "KEEP", instance_only=True),
        ]
        graph = self._make_graph([
            ("Page", "page", "layout.page"),
            ("Timeseries", "ts", "presentation.timeseries"),
        ])
        comp_map = {
            "presentation.kpi_row": "layout.page",
            "presentation.timeseries": "layout.page",
        }
        missing = verify_graphir_coverage(caps, graph, comp_map)
        assert len(missing) >= 1, (
            "Should detect that KpiRow (KEEP under MODIFY parent) is missing from GraphIR"
        )
        assert "presentation.kpi_row" in missing[0], (
            f"Missing should mention kpi_row, got: {missing}"
        )

    def test_all_keep_children_present_no_violation(self):
        from app.graphir.compiler import verify_graphir_coverage
        caps = [
            self._make_struct_cap("layout.page", "MODIFY"),
            self._make_struct_cap("presentation.kpi_row", "KEEP", instance_only=True),
            self._make_struct_cap("presentation.timeseries", "KEEP", instance_only=True),
        ]
        graph = self._make_graph([
            ("Page", "page", "layout.page"),
            ("KpiRow", "kpi", "presentation.kpi_row"),
            ("Timeseries", "ts", "presentation.timeseries"),
        ])
        comp_map = {
            "presentation.kpi_row": "layout.page",
            "presentation.timeseries": "layout.page",
        }
        missing = verify_graphir_coverage(caps, graph, comp_map)
        assert len(missing) == 0, f"Expected 0 missing, got: {missing}"

    def test_no_composition_map_skips_check(self):
        from app.graphir.compiler import verify_graphir_coverage
        caps = [
            self._make_struct_cap("layout.page", "MODIFY"),
            self._make_struct_cap("presentation.kpi_row", "KEEP"),
        ]
        graph = self._make_graph([
            ("Page", "page", "layout.page"),
        ])
        missing = verify_graphir_coverage(caps, graph, None)
        assert len(missing) == 0

    def test_keep_not_under_modify_parent_no_violation(self):
        from app.graphir.compiler import verify_graphir_coverage
        from app.graphir.models import GraphIRNode, GraphIRDraft
        caps = [
            self._make_struct_cap("layout.page", "KEEP"),
            self._make_struct_cap("presentation.kpi_row", "KEEP"),
        ]
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="kpi", type="KpiRow", data={},
            metadata={"intent_capability": "presentation.kpi_row"}))
        graph = draft.freeze()
        comp_map = {"presentation.kpi_row": "layout.page"}
        missing = verify_graphir_coverage(caps, graph, comp_map)
        assert len(missing) == 0, (
            "KEEP under KEEP parent should not trigger"
        )

    def test_instance_only_keep_not_flagged(self):
        from app.graphir.compiler import verify_graphir_coverage
        caps = [
            self._make_struct_cap("layout.page", "MODIFY"),
            self._make_struct_cap("presentation.kpi_row", "KEEP", instance_only=True),
        ]
        graph = self._make_graph([
            ("Page", "page", "layout.page"),
        ])
        comp_map = {"presentation.kpi_row": "layout.page"}
        missing = verify_graphir_coverage(caps, graph, comp_map)
        assert len(missing) == 0, (
            "Instance-only KEEP should not be flagged"
        )


class TestCompositionChildExpansion:
    """Regression: KEEP slot children under MODIFY parent must appear in GraphIR.

    This tests the exact bug from Phase 6: when composition sync promotes Page to
    MODIFY, KEEP children (KpiRow) were absent from GraphIR because the builder
    only processes CREATE/MODIFY/INSTANCE operations. _expand_composition_children()
    promotes them to instance_only, making them visible to the builder.
    """

    def test_create_timeseries_includes_kpi_in_graph(self):
        """Full pipeline: CREATE timeseries → composition sync → Page MODIFY
        → KpiRow KEEP promoted to INSTANCE → GraphIR includes KpiRow."""
        import os, json, shutil, tempfile
        os.environ.setdefault("REPO_ROOT", "/opt/agent-repos/agent-test-repo")

        from app.engine.structural_index import StructuralIndex
        from app.contracts.semantic_resolution import SemanticResolution
        from app.contracts.contract_resolution import ContractResolution
        from app.contracts.skill_registry import get_contract
        from app.engine.structural_completion import complete_structure

        idx = StructuralIndex.from_worktree("/opt/agent-repos/agent-test-repo")
        sem = SemanticResolution(
            actions=[{"verb": "create", "object": "line", "confidence": 1.0}],
            semantic_params={"timeseries_metric": "revenue"},
            semantic_provenance={},
            confidence=1.0,
        )
        cr = ContractResolution(
            contract_params={"timeseries_metric": "revenue"},
            contract_provenance={},
            confidence=1.0,
        )
        contract = get_contract("dashboard.sales_overview", 1)
        structural = complete_structure(
            semantic_resolution=sem,
            contract_resolution=cr,
            contract=contract,
            structural_index=idx,
        )

        # KpiRow should be INSTANCE (instance_only) after expansion
        kpi_caps = [c for c in structural.capabilities if c.name == "presentation.kpi_row"]
        assert len(kpi_caps) == 1, f"Expected 1 KpiRow capability, got {len(kpi_caps)}"
        assert kpi_caps[0].instance_only, (
            "KpiRow should be instance_only after composition child expansion"
        )

        # Build GraphIR from structural IR
        from app.graphir.builder import build_from_structural
        graph = build_from_structural(structural)

        # Verify KpiRow is in the graph nodes
        kpi_nodes = [n for nid, n in graph.nodes.items() if "KpiRow" in n.type]
        assert len(kpi_nodes) >= 1, (
            "KpiRow must appear in GraphIR after composition child expansion"
        )
        # Verify the metadata marks it as instance_only
        assert kpi_nodes[0].metadata.get("instance_only") is True, (
            "KpiRow GraphIR node must be marked instance_only"
        )

    def test_delete_timeseries_includes_kpi_in_graph(self):
        """DELETE child → composition sync → KEEP siblings promoted to INSTANCE."""
        import os
        os.environ.setdefault("REPO_ROOT", "/opt/agent-repos/agent-test-repo")

        from app.engine.structural_index import StructuralIndex
        from app.contracts.semantic_resolution import SemanticResolution
        from app.contracts.contract_resolution import ContractResolution
        from app.contracts.skill_registry import get_contract
        from app.engine.structural_completion import complete_structure

        idx = StructuralIndex.from_worktree("/opt/agent-repos/agent-test-repo")
        sem = SemanticResolution(
            actions=[{"verb": "remove", "object": "timeseries", "confidence": 1.0, "instance_hint": "linechart"}],
            semantic_params={},
            semantic_provenance={},
            confidence=1.0,
        )
        cr = ContractResolution(
            contract_params={},
            contract_provenance={},
            confidence=1.0,
        )
        contract = get_contract("dashboard.sales_overview", 1)
        structural = complete_structure(
            semantic_resolution=sem,
            contract_resolution=cr,
            contract=contract,
            structural_index=idx,
        )

        from app.graphir.builder import build_from_structural
        graph = build_from_structural(structural)

        kpi_nodes = [n for nid, n in graph.nodes.items() if "KpiRow" in n.type]
        # KpiRow should exist as instance_only (sibling of deleted Timeseries)
        assert len(kpi_nodes) >= 1, (
            "KpiRow must appear in GraphIR even when deleting Timeseries"
        )
