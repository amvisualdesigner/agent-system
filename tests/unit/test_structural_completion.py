"""Tests for StructuralCompletionLayer."""

from __future__ import annotations

import pytest

from app.engine.structural_completion import (
    CompletionMode,
    ResolvedCapability,
    StructuralIR,
    STRUCTURAL_SCHEMA,
    _infer_capabilities_from_contract,
    _augment_capabilities,
    _resolve_completion_mode,
    _resolve_safe_default,
    complete_structure,
    graphir_ready_to_intent_plan,
)
from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.skill_registry import SkillContract


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def dashboard_contract():
    return SkillContract(
        contract_id="dashboard.sales_overview",
        version=1,
        input_schema={
            "type": "object",
            "required": ["metrics"],
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["revenue", "growth", "retention", "churn"]},
                },
                "timeseries_metric": {
                    "type": "string",
                    "enum": ["revenue", "growth", "retention"],
                    "default": "revenue",
                },
            },
        },
        ast_template={
            "layout": "AnalyticsGrid",
            "slots": [
                {"type": "KpiRow", "props": {"metrics": "metrics"}},
                {"type": "Timeseries", "props": {"metric": "timeseries_metric"}},
            ],
            "capabilities": {
                "KpiRow": "presentation.kpi_row",
                "Timeseries": "presentation.timeseries",
                "Page": "layout.page",
                "Domain": "domain.sales",
            },
        },
        renderer={},
    )


@pytest.fixture
def table_contract():
    return SkillContract(
        contract_id="analytics.table",
        version=1,
        input_schema={
            "type": "object",
            "required": ["columns"],
            "properties": {
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        ast_template={
            "layout": None,
            "slots": [
                {"type": "AnalyticsTable", "props": {"columns": "columns"}},
            ],
            "capabilities": {
                "AnalyticsTable": "presentation.table",
            },
        },
        renderer={},
    )


def make_resolution(
    contract_id: str = "dashboard.sales_overview",
    params: dict | None = None,
    provenance: dict | None = None,
    confidence: float = 0.8,
):
    return SemanticResolution(
        contract_id=contract_id,
        contract_version=1,
        params=params or {},
        param_provenance=provenance or {},
        confidence=confidence,
    )


def _cap(ir: StructuralIR, name: str) -> ResolvedCapability:
    """Find ResolvedCapability by name in StructuralIR."""
    for rc in ir.capabilities:
        if rc.name == name:
            return rc
    raise KeyError(name)


def _cap_names(ir: StructuralIR) -> list[str]:
    """Return capability names from StructuralIR."""
    return [rc.name for rc in ir.capabilities]


# ═══════════════════════════════════════════════════════════════════
# Tests: _infer_capabilities_from_contract
# ═══════════════════════════════════════════════════════════════════

class TestInferCapabilities:
    def test_dashboard_contract_caps(self, dashboard_contract):
        caps = _infer_capabilities_from_contract(dashboard_contract)
        assert "presentation.kpi_row" in caps
        assert "presentation.timeseries" in caps
        assert "layout.page" in caps
        assert "domain.sales" in caps
        assert len(caps) == 4

    def test_table_contract_caps(self, table_contract):
        caps = _infer_capabilities_from_contract(table_contract)
        assert caps == ["presentation.table"]

    def test_no_capabilities(self):
        contract = SkillContract(
            contract_id="noop", version=1,
            input_schema={}, ast_template={}, renderer={},
        )
        caps = _infer_capabilities_from_contract(contract)
        assert caps == []


# ═══════════════════════════════════════════════════════════════════
# Tests: _augment_capabilities
# ═══════════════════════════════════════════════════════════════════

class TestAugmentCapabilities:
    def test_no_frame_no_change(self):
        resolution = make_resolution()
        result = _augment_capabilities(["presentation.kpi_row"], resolution, None)
        assert result == ["presentation.kpi_row"]

    def test_table_hint_adds_table_capability(self):
        resolution = make_resolution(params={"mentioned_metrics": ["revenue"]})
        frame = {
            "objects": [{"type": "table", "confidence": 0.9}],
            "actions": [],
        }
        result = _augment_capabilities(["presentation.kpi_row"], resolution, frame)
        assert "presentation.table" in result
        assert "presentation.kpi_row" in result

    def test_no_table_object_no_add(self):
        resolution = make_resolution(params={"mentioned_metrics": ["revenue"]})
        frame = {
            "objects": [{"type": "kpi_row", "confidence": 0.9}],
            "actions": [],
        }
        result = _augment_capabilities(["presentation.kpi_row"], resolution, frame)
        assert result == ["presentation.kpi_row"]

    def test_table_object_no_signal_no_add(self):
        resolution = make_resolution()
        frame = {
            "objects": [{"type": "table", "confidence": 0.9}],
            "actions": [],
        }
        result = _augment_capabilities(["presentation.kpi_row"], resolution, frame)
        assert result == ["presentation.kpi_row"]

    def test_deduplicates(self):
        resolution = make_resolution(params={"columns": ["id"]})
        frame = {
            "objects": [{"type": "table", "confidence": 0.9}],
            "actions": [],
        }
        result = _augment_capabilities(
            ["presentation.kpi_row", "presentation.table"], resolution, frame,
        )
        # Should not duplicate
        assert result == ["presentation.kpi_row", "presentation.table"]


# ═══════════════════════════════════════════════════════════════════
# Tests: _resolve_completion_mode
# ═══════════════════════════════════════════════════════════════════

class TestResolveCompletionMode:
    def test_presentation_always_complete(self):
        mode = _resolve_completion_mode("presentation.kpi_row", 0.3)
        assert mode == CompletionMode.SAFE_COMPLETE

    def test_domain_high_confidence_strict_fail(self):
        mode = _resolve_completion_mode("domain.sales", 0.6)
        assert mode == CompletionMode.STRICT_FAIL

    def test_domain_low_confidence_safe_skip(self):
        mode = _resolve_completion_mode("domain.sales", 0.59)
        assert mode == CompletionMode.SAFE_SKIP

    def test_unknown_capability_safe_skip(self):
        mode = _resolve_completion_mode("unknown.capability", 1.0)
        assert mode == CompletionMode.SAFE_SKIP

    def test_layout_safe_complete(self):
        mode = _resolve_completion_mode("layout.page", 0.0)
        assert mode == CompletionMode.SAFE_COMPLETE


# ═══════════════════════════════════════════════════════════════════
# Tests: _resolve_safe_default
# ═══════════════════════════════════════════════════════════════════

class TestResolveSafeDefault:
    def test_kpi_metrics_default(self):
        assert _resolve_safe_default("presentation.kpi_row", "metrics") == ["net_revenue"]

    def test_table_columns_default(self):
        assert _resolve_safe_default("presentation.table", "columns") == ["id"]

    def test_timeseries_metric_default(self):
        assert _resolve_safe_default("presentation.timeseries", "metric") == "revenue"

    def test_domain_never_auto_completes(self):
        with pytest.raises(ValueError, match="Cannot auto-complete domain field"):
            _resolve_safe_default("domain.sales", "metrics")

    def test_unknown_field_no_default(self):
        with pytest.raises(ValueError, match="No safe default available"):
            _resolve_safe_default("presentation.kpi_row", "nonexistent")


# ═══════════════════════════════════════════════════════════════════
# Tests: complete_structure — STRICT_FAIL path (no longer raises for domain.*)
# ═══════════════════════════════════════════════════════════════════

class TestCompleteStructureStrictFail:
    """domain.* with missing required → SAFE_SKIP (no crash, no incomplete flag)."""

    def test_domain_high_confidence_missing_required_no_raise(self, dashboard_contract):
        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.8,
        )
        ready = complete_structure(resolution, dashboard_contract)
        rc = _cap(ready, "domain.sales")
        assert rc.mode == CompletionMode.SAFE_SKIP
        assert rc.params == {}

    def test_domain_all_required_present_passes(self, dashboard_contract):
        resolution = make_resolution(
            params={"metrics": ["net_revenue"], "dimensions": ["region"]},
            confidence=0.8,
        )
        ready = complete_structure(resolution, dashboard_contract)
        assert _cap(ready, "domain.sales").params["metrics"] == ["net_revenue"]
        assert _cap(ready, "domain.sales").params["dimensions"] == ["region"]
        completed = [c for c in ready.capabilities if c.name == "domain.sales"]
        assert len(completed) == 1
        assert completed[0].mode != CompletionMode.SAFE_SKIP



# ═══════════════════════════════════════════════════════════════════
# Tests: complete_structure — SAFE_SKIP path (domain low confidence)
# ═══════════════════════════════════════════════════════════════════

class TestCompleteStructureSafeSkip:
    def test_domain_low_confidence_skips_node(self, dashboard_contract):
        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        ready = complete_structure(resolution, dashboard_contract)
        rc = _cap(ready, "domain.sales")
        assert rc.mode == CompletionMode.SAFE_SKIP
        assert rc.params == {}
        assert any("domain.sales" in w for w in ready.completion_warnings)

    def test_presentation_still_completed_when_domain_skipped(self, dashboard_contract):
        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        ready = complete_structure(resolution, dashboard_contract)
        assert _cap(ready, "presentation.kpi_row").params["metrics"] == ["net_revenue"]


# ═══════════════════════════════════════════════════════════════════
# Tests: complete_structure — SAFE_COMPLETE path
# ═══════════════════════════════════════════════════════════════════

class TestCompleteStructureSafeComplete:
    def test_kpi_metrics_from_resolution(self, dashboard_contract):
        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,  # domain.sales → SAFE_SKIP
        )
        ready = complete_structure(resolution, dashboard_contract)
        assert _cap(ready, "presentation.kpi_row").params["metrics"] == ["net_revenue"]

    def test_timeseries_metric_safe_default(self, dashboard_contract):
        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        ready = complete_structure(resolution, dashboard_contract)
        assert _cap(ready, "presentation.timeseries").params["metric"] == "revenue"

    def test_timeseries_metric_from_resolution(self, dashboard_contract):
        resolution = make_resolution(
            params={"metrics": ["net_revenue"], "time_granularity": "monthly"},
            confidence=0.5,
        )
        ready = complete_structure(resolution, dashboard_contract)
        assert _cap(ready, "presentation.timeseries").params["time_granularity"] == "monthly"

    def test_page_no_required_params(self, dashboard_contract):
        resolution = make_resolution(confidence=0.5)
        ready = complete_structure(resolution, dashboard_contract)
        assert _cap(ready, "layout.page").params == {}

    def test_table_columns_safe_default(self, table_contract):
        resolution = make_resolution(contract_id="analytics.table")
        ready = complete_structure(resolution, table_contract)
        assert _cap(ready, "presentation.table").params["columns"] == ["id"]

    def test_table_columns_from_resolution(self, table_contract):
        resolution = make_resolution(
            contract_id="analytics.table",
            params={"columns": ["revenue", "growth"]},
        )
        ready = complete_structure(resolution, table_contract)
        assert _cap(ready, "presentation.table").params["columns"] == ["revenue", "growth"]


# ═══════════════════════════════════════════════════════════════════
# Tests: complete_structure — hint augmentation
# ═══════════════════════════════════════════════════════════════════

class TestCompleteStructureHintAugmentation:
    def test_table_hint_adds_table_to_dashboard_contract(self, dashboard_contract):
        resolution = make_resolution(
            params={"mentioned_metrics": ["revenue"]},
            confidence=0.5,
        )
        frame = {
            "objects": [{"type": "table", "confidence": 0.9}],
            "actions": [{"verb": "show", "object": "table", "confidence": 0.8}],
        }
        ready = complete_structure(resolution, dashboard_contract, frame_dict=frame)
        assert "presentation.table" in _cap_names(ready)

    def test_table_hint_with_columns_from_resolution(self, dashboard_contract):
        resolution = make_resolution(
            params={"columns": ["revenue"], "metrics": ["net_revenue"]},
            confidence=0.5,
        )
        frame = {
            "objects": [{"type": "table", "confidence": 0.9}],
            "actions": [],
        }
        ready = complete_structure(resolution, dashboard_contract, frame_dict=frame)
        assert "presentation.table" in _cap_names(ready)
        assert "columns" in _cap(ready, "presentation.table").params


# ═══════════════════════════════════════════════════════════════════
# Tests: StructuralIR output structure
# ═══════════════════════════════════════════════════════════════════

class TestStructuralIR:
    def test_plan_has_expected_fields(self, dashboard_contract):
        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        ready = complete_structure(resolution, dashboard_contract)
        assert ready.contract_id == "dashboard.sales_overview"
        assert ready.confidence == 0.5
        assert isinstance(ready.capabilities, list)
        assert isinstance(ready.param_provenance, dict)
        assert isinstance(ready.completion_warnings, list)

    def test_no_ratio_in_any_params(self, dashboard_contract):
        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        ready = complete_structure(resolution, dashboard_contract)
        all_params = ""
        for rc in ready.capabilities:
            all_params += str(rc.params)
        assert "ratio" not in all_params

    def test_no_churn_in_any_params(self, dashboard_contract):
        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        ready = complete_structure(resolution, dashboard_contract)
        all_params = ""
        for rc in ready.capabilities:
            all_params += str(rc.params)
        assert "churn" not in all_params

    def test_warnings_for_safe_defaults(self, dashboard_contract):
        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        ready = complete_structure(resolution, dashboard_contract)
        warnings = " ".join(ready.completion_warnings)
        assert "presentation.timeseries" in warnings
        assert "safe default" in warnings

    def test_all_capabilities_list(self, dashboard_contract):
        """All 4 capabilities appear in capabilities list (SAFE_SKIP included)."""
        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        ready = complete_structure(resolution, dashboard_contract)
        assert len(ready.capabilities) == 4
        rc = _cap(ready, "domain.sales")
        assert rc.mode == CompletionMode.SAFE_SKIP
        assert rc.params == {}

    def test_graphir_ready_to_intent_plan_skips_safe_skip(self, dashboard_contract):
        """SAFE_SKIP capabilities are excluded from the IntentPlan."""
        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        ready = complete_structure(resolution, dashboard_contract)
        plan = graphir_ready_to_intent_plan(ready)
        assert not any(i.capability == "domain.sales" for i in plan.intents)
        assert any(i.capability == "presentation.kpi_row" for i in plan.intents)


# ═══════════════════════════════════════════════════════════════════
# Tests: STRUCTURAL_SCHEMA integrity
# ═══════════════════════════════════════════════════════════════════

class TestStructuralSchemaIntegrity:
    def test_all_schemas_have_required_list(self):
        for cap, schema in STRUCTURAL_SCHEMA.items():
            assert "required" in schema, f"{cap} missing required"
            assert isinstance(schema["required"], list), f"{cap} required not list"

    def test_all_schemas_have_safe_fallback(self):
        for cap, schema in STRUCTURAL_SCHEMA.items():
            assert "safe_fallback" in schema, f"{cap} missing safe_fallback"
            assert isinstance(schema["safe_fallback"], dict), f"{cap} safe_fallback not dict"

    def test_domain_safe_fallback_empty(self):
        for cap, schema in STRUCTURAL_SCHEMA.items():
            if cap.startswith("domain."):
                assert schema["safe_fallback"] == {}, f"{cap} should have empty safe_fallback"

    def test_domain_mode_strict_fail(self):
        for cap, schema in STRUCTURAL_SCHEMA.items():
            if cap.startswith("domain."):
                assert schema["mode"] == CompletionMode.STRICT_FAIL, f"{cap} should be STRICT_FAIL"


# ═══════════════════════════════════════════════════════════════════
# Test: Pipeline invariants (ARCHITECTURAL — the one that protects you)
# ═══════════════════════════════════════════════════════════════════

class TestPipelineInvariants:
    """Single test that validates all 4 architectural invariants end-to-end.

    Si este test pasa, el pipeline respeta:
      1. Semantic layer correctness
      2. StructuralIR ownership integrity (no triple dict)
      3. No flattening leak
      4. UI IR purity (no binding redistribution)
    """

    def test_full_pipeline_invariants(self, dashboard_contract):
        from unittest.mock import patch

        resolution = make_resolution(
            params={"metrics": ["net_revenue"], "time_granularity": "monthly"},
            confidence=0.8,
        )

        # ── 1. Semantic layer correctness ──
        assert resolution.params["metrics"] == ["net_revenue"]
        assert resolution.params["time_granularity"] == "monthly"

        # ── 2. StructuralIR ownership integrity ──
        structural_ir = complete_structure(resolution, dashboard_contract)
        assert len(structural_ir.capabilities) == 4
        assert all(isinstance(c.params, dict) for c in structural_ir.capabilities)

        kpi = next(c for c in structural_ir.capabilities if c.name == "presentation.kpi_row")
        assert kpi.params == {"metrics": ["net_revenue"]}
        assert kpi.mode == CompletionMode.SAFE_COMPLETE

        # ── 3. No flattening leak ──
        assert not hasattr(structural_ir, "flat_params")
        assert graphir_ready_to_intent_plan(structural_ir).params == {}

        # ── 4a. UI IR purity: run via new path, verify node data matches capability params ──
        from app.graphir.pipeline import GraphIRPipeline
        from app.graphir.builder import GraphIRBuilder

        # Patch forbidden functions *before* running — this catches regressions
        with patch.object(GraphIRBuilder, "build") as mock_build:
            graph, _ = GraphIRPipeline.run_from_structural(structural_ir)

            # The legacy build() must NOT be called from the new path
            mock_build.assert_not_called()

        # Verify actual node data (deep_freeze converts lists → tuples)
        kpi_node = graph.nodes.get("KpiRow_1")
        assert kpi_node is not None, "Expected KpiRow_1 node in graph"
        assert kpi_node.data == {"metrics": ("net_revenue",)}

        # ── 4b. Trace: verify forbidden code paths were never executed ──
        from app.graphir import binding as binding_module
        from app.graphir import builder as builder_module

        with (
            patch.object(binding_module, "bind_skillir_to_nodes") as mock_bind,
            patch.object(binding_module, "validate_binding") as mock_val,
            patch.object(builder_module.GraphIRBuilder, "build") as mock_build,
        ):
            # Run again through the new path
            GraphIRPipeline.run_from_structural(structural_ir)
            mock_bind.assert_not_called()
            mock_val.assert_not_called()
            mock_build.assert_not_called()

        # ── 5. StructuralCoverageValidator integrity ──
        from app.graphir.structural_coverage import StructuralCoverageValidator
        sreport = StructuralCoverageValidator.validate(structural_ir, dashboard_contract)
        assert sreport.is_valid
        assert sreport.completeness > 0
        # domain.sales is SAFE_SKIP (missing dimensions, high confidence but incomplete)
        assert sreport.safe_skip_count == 1

        # ── 6. CHECK 2 — Structural determinism ──
        # SAFE_COMPLETE capabilities must have their required fields
        # layout.page is SAFE_COMPLETE with params={} (no required fields — valid)
        from app.engine.structural_completion import STRUCTURAL_SCHEMA
        for cap in structural_ir.capabilities:
            if cap.mode == CompletionMode.SAFE_COMPLETE:
                schema = STRUCTURAL_SCHEMA.get(cap.name, {})
                required = schema.get("required", [])
                if required:
                    assert all(
                        r in cap.params for r in required
                    ), f"{cap.name}: missing required field in SAFE_COMPLETE params"
