"""Tests for StructuralCompletionLayer.

Architecture:
  SemanticResolution (language) + ContractResolution (contract)
  → StructuralIR (ownership)

Structural layer NEVER invents domain semantics.
"""

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
    complete_structure,
)
from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.contract_resolution import ContractResolution
from app.contracts.skill_registry import SkillContract, get_contract


def _find_node_by_type(graph, node_type: str):
    for nid, node in graph.nodes.items():
        if node.type == node_type:
            return node
    return None


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


def make_semantic(
    params: dict | None = None,
    provenance: dict | None = None,
    confidence: float = 0.8,
    actions: list[dict] | None = None,
) -> SemanticResolution:
    return SemanticResolution(
        semantic_params=params or {},
        semantic_provenance=provenance or {},
        confidence=confidence,
        actions=actions or [],
    )


def make_contract(
    contract_id: str = "dashboard.sales_overview",
    params: dict | None = None,
    confidence: float = 0.8,
) -> ContractResolution:
    return ContractResolution(
        contract_params=params or {},
        contract_provenance={k: "test" for k in (params or {})},
        confidence=confidence,
        contract_id=contract_id,
        contract_version=1,
    )


def _cap(ir: StructuralIR, name: str) -> ResolvedCapability:
    """Find ResolvedCapability by name in StructuralIR."""
    for rc in ir.capabilities:
        if rc.name == name:
            return rc
    raise KeyError(name)


def _cap_names(ir: StructuralIR) -> list[str]:
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
        semantic = make_semantic()
        contract = make_contract()
        result = _augment_capabilities(
            ["presentation.kpi_row"], semantic, contract, None,
        )
        assert result == ["presentation.kpi_row"]

    def test_table_hint_adds_table_capability(self):
        semantic = make_semantic(params={"mentioned_metrics": ["revenue"]})
        contract = make_contract()
        frame = {
            "objects": [{"type": "table", "confidence": 0.9}],
            "actions": [],
        }
        result = _augment_capabilities(
            ["presentation.kpi_row"], semantic, contract, frame,
        )
        assert "presentation.table" in result
        assert "presentation.kpi_row" in result

    def test_no_table_object_no_add(self):
        semantic = make_semantic(params={"mentioned_metrics": ["revenue"]})
        contract = make_contract()
        frame = {
            "objects": [{"type": "kpi_row", "confidence": 0.9}],
            "actions": [],
        }
        result = _augment_capabilities(
            ["presentation.kpi_row"], semantic, contract, frame,
        )
        assert result == ["presentation.kpi_row"]

    def test_table_object_no_signal_no_add(self):
        semantic = make_semantic()
        contract = make_contract()
        frame = {
            "objects": [{"type": "table", "confidence": 0.9}],
            "actions": [],
        }
        result = _augment_capabilities(
            ["presentation.kpi_row"], semantic, contract, frame,
        )
        assert result == ["presentation.kpi_row"]

    def test_deduplicates(self):
        semantic = make_semantic(params={"columns": ["id"]})
        contract = make_contract()
        frame = {
            "objects": [{"type": "table", "confidence": 0.9}],
            "actions": [],
        }
        result = _augment_capabilities(
            ["presentation.kpi_row", "presentation.table"],
            semantic, contract, frame,
        )
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
# Tests: complete_structure — STRICT_FAIL path
# ═══════════════════════════════════════════════════════════════════

class TestCompleteStructureStrictFail:
    """domain.* with missing required → SAFE_SKIP (no crash)."""

    def test_domain_high_confidence_missing_required_skips(self, dashboard_contract):
        semantic = make_semantic(params={"metrics": ["net_revenue"]})
        contract = make_contract(params={"metrics": ["net_revenue"]}, confidence=0.8)
        ready = complete_structure(semantic, contract, dashboard_contract)
        rc = _cap(ready, "domain.sales")
        assert rc.mode == CompletionMode.SAFE_SKIP
        assert rc.params == {}

    def test_domain_modify_all_required_present(self, dashboard_contract):
        """Phase 3: explicit modify intent resolves params and produces STRICT_FAIL mode."""
        semantic = make_semantic(
            params={"metrics": ["net_revenue"], "dimensions": ["region"]},
            actions=[{"verb": "modify", "object": "sales", "confidence": 0.8}],
        )
        contract = make_contract(
            params={"metrics": ["net_revenue"], "dimensions": ["region"]},
        )
        ready = complete_structure(semantic, contract, dashboard_contract)
        assert _cap(ready, "domain.sales").params["metrics"] == ["net_revenue"]
        assert _cap(ready, "domain.sales").params["dimensions"] == ["region"]
        assert _cap(ready, "domain.sales").mode != CompletionMode.SAFE_SKIP


# ═══════════════════════════════════════════════════════════════════
# Tests: complete_structure — SAFE_SKIP path
# ═══════════════════════════════════════════════════════════════════

class TestCompleteStructureSafeSkip:
    def test_domain_low_confidence_skips_node(self, dashboard_contract):
        """Phase 3: explicit modify + low confidence → SAFE_SKIP + warning."""
        semantic = make_semantic(
            params={"metrics": ["net_revenue"]}, confidence=0.5,
            actions=[{"verb": "modify", "object": "sales", "confidence": 0.5}],
        )
        contract = make_contract(params={"metrics": ["net_revenue"]}, confidence=0.5)
        ready = complete_structure(semantic, contract, dashboard_contract)
        rc = _cap(ready, "domain.sales")
        assert rc.mode == CompletionMode.SAFE_SKIP
        assert rc.params == {}
        assert any("domain.sales" in w for w in ready.completion_warnings)

    def test_presentation_still_completed_when_domain_skipped(self, dashboard_contract):
        """Phase 3: explicit modify on kpi resolves params even when domain is skipped."""
        semantic = make_semantic(
            params={"metrics": ["net_revenue"]}, confidence=0.5,
            actions=[{"verb": "modify", "object": "kpi", "confidence": 0.5}],
        )
        contract = make_contract(params={"metrics": ["net_revenue"]}, confidence=0.5)
        ready = complete_structure(semantic, contract, dashboard_contract)
        assert _cap(ready, "presentation.kpi_row").params["metrics"] == ["net_revenue"]


# ═══════════════════════════════════════════════════════════════════
# Tests: complete_structure — SAFE_COMPLETE path
# ═══════════════════════════════════════════════════════════════════

class TestCompleteStructureSafeComplete:
    def test_kpi_metrics_from_semantic(self, dashboard_contract):
        """Phase 3: explicit modify intent resolves params."""
        semantic = make_semantic(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
            actions=[{"verb": "modify", "object": "kpi", "confidence": 0.5}],
        )
        contract = make_contract(params={"metrics": ["net_revenue"]}, confidence=0.5)
        ready = complete_structure(semantic, contract, dashboard_contract)
        assert _cap(ready, "presentation.kpi_row").params["metrics"] == ["net_revenue"]

    def test_timeseries_metric_from_contract_slot_mapping(self, dashboard_contract):
        """timeseries gets metric via slot mapping: metric ← timeseries_metric."""
        semantic = make_semantic(
            confidence=0.5,
            actions=[{"verb": "modify", "object": "timeseries", "confidence": 0.5}],
        )
        contract = make_contract(
            params={"timeseries_metric": "growth"},
            confidence=0.5,
        )
        ready = complete_structure(semantic, contract, dashboard_contract)
        assert _cap(ready, "presentation.timeseries").params["metric"] == "growth"

    def test_timeseries_metric_via_contract_default(self, dashboard_contract):
        """Without SkillIR, contract default 'revenue' flows via slot mapping."""
        semantic = make_semantic(
            confidence=0.5,
            actions=[{"verb": "modify", "object": "timeseries", "confidence": 0.5}],
        )
        # Create ContractResolution from SkillIR without timeseries_metric
        from app.contracts.skill_ir import SkillIR
        skill_ir = SkillIR(
            contract_id="dashboard.sales_overview",
            confidence=0.5,
            params={"metrics": ["net_revenue"]},
            version=1,
        )
        contract = ContractResolution.from_skillir(skill_ir, dashboard_contract)
        ready = complete_structure(semantic, contract, dashboard_contract)
        # Contract default "revenue" applied via slot mapping
        assert _cap(ready, "presentation.timeseries").params["metric"] == "revenue"

    def test_timeseries_missing_metric_uses_contract_default(self, dashboard_contract):
        """No semantic, no contract params → contract input_schema default 'revenue' via slot mapping."""
        semantic = make_semantic(
            confidence=0.5,
            actions=[{"verb": "modify", "object": "timeseries", "confidence": 0.5}],
        )
        contract = make_contract(confidence=0.5)
        ready = complete_structure(semantic, contract, dashboard_contract)
        rc = _cap(ready, "presentation.timeseries")
        assert rc.mode == CompletionMode.SAFE_COMPLETE
        assert rc.params["metric"] == "revenue"
        assert rc.params == {"metric": "revenue"}

    def test_timeseries_time_granularity_from_semantic(self, dashboard_contract):
        """Phase 3: explicit modify intent resolves params from semantic and contract."""
        semantic = make_semantic(
            params={"time_granularity": "monthly"},
            confidence=0.5,
            actions=[{"verb": "modify", "object": "timeseries", "confidence": 0.5}],
        )
        contract = make_contract(params={"timeseries_metric": "revenue"}, confidence=0.5)
        ready = complete_structure(semantic, contract, dashboard_contract)
        assert _cap(ready, "presentation.timeseries").params["time_granularity"] == "monthly"
        assert _cap(ready, "presentation.timeseries").params["metric"] == "revenue"

    def test_page_no_required_params(self, dashboard_contract):
        semantic = make_semantic(confidence=0.5)
        contract = make_contract(confidence=0.5)
        ready = complete_structure(semantic, contract, dashboard_contract)
        assert _cap(ready, "layout.page").params == {}

    def test_table_columns_missing_skips(self, table_contract):
        semantic = make_semantic(confidence=0.5)
        contract = make_contract(confidence=0.5)
        ready = complete_structure(semantic, contract, table_contract)
        rc = _cap(ready, "presentation.table")
        assert rc.mode == CompletionMode.SAFE_SKIP
        assert rc.params == {}

    def test_table_columns_from_semantic(self, table_contract):
        """Phase 3: explicit modify intent resolves table columns from semantic."""
        semantic = make_semantic(
            params={"columns": ["revenue", "growth"]},
            actions=[{"verb": "modify", "object": "table", "confidence": 0.8}],
        )
        contract = make_contract(params={"columns": ["revenue", "growth"]})
        ready = complete_structure(semantic, contract, table_contract)
        assert _cap(ready, "presentation.table").params["columns"] == ["revenue", "growth"]

    def test_kpi_metrics_from_contract_when_semantic_empty(self, dashboard_contract):
        """When semantic layer has no metrics, contract provides them via slot mapping."""
        semantic = make_semantic(
            confidence=0.5,
            actions=[{"verb": "modify", "object": "kpi", "confidence": 0.5}],
        )
        contract = make_contract(params={"metrics": ["net_revenue"]}, confidence=0.5)
        ready = complete_structure(semantic, contract, dashboard_contract)
        assert _cap(ready, "presentation.kpi_row").params["metrics"] == ["net_revenue"]

    def test_semantic_wins_over_contract(self, dashboard_contract):
        """Semantic (language) wins over contract (SkillIR/defaults)."""
        semantic = make_semantic(
            params={"metrics": ["user_specified_metric"]},
            confidence=0.8,
            actions=[{"verb": "modify", "object": "kpi", "confidence": 0.8}],
        )
        contract = make_contract(params={"metrics": ["contract_default"]}, confidence=0.8)
        ready = complete_structure(semantic, contract, dashboard_contract)
        assert _cap(ready, "presentation.kpi_row").params["metrics"] == ["user_specified_metric"]


# ═══════════════════════════════════════════════════════════════════
# Tests: complete_structure — hint augmentation
# ═══════════════════════════════════════════════════════════════════

class TestCompleteStructureHintAugmentation:
    def test_table_hint_adds_table_to_dashboard_contract(self, dashboard_contract):
        semantic = make_semantic(
            params={"mentioned_metrics": ["revenue"]},
            confidence=0.5,
        )
        contract = make_contract(confidence=0.5)
        frame = {
            "objects": [{"type": "table", "confidence": 0.9}],
            "actions": [{"verb": "show", "object": "table", "confidence": 0.8}],
        }
        ready = complete_structure(semantic, contract, dashboard_contract, frame_dict=frame)
        assert "presentation.table" in _cap_names(ready)

    def test_table_hint_with_columns_from_semantic(self, dashboard_contract):
        """Phase 3: frame augments capabilities in declarative mode, but no intent → KEEP + empty params."""
        semantic = make_semantic(
            params={"columns": ["revenue"], "metrics": ["net_revenue"]},
            confidence=0.5,
        )
        contract = make_contract(
            params={"columns": ["revenue"], "metrics": ["net_revenue"]},
            confidence=0.5,
        )
        frame = {
            "objects": [{"type": "table", "confidence": 0.9}],
            "actions": [],
        }
        ready = complete_structure(semantic, contract, dashboard_contract, frame_dict=frame)
        assert "presentation.table" in _cap_names(ready)
        # Phase 3: no intent → KEEP → empty params (no provisioning without intent)
        rc = _cap(ready, "presentation.table")
        assert rc.action == "KEEP"
        assert rc.params == {}


# ═══════════════════════════════════════════════════════════════════
# Tests: StructuralIR output structure
# ═══════════════════════════════════════════════════════════════════

class TestStructuralIR:
    def test_plan_has_expected_fields(self, dashboard_contract):
        semantic = make_semantic(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        contract = make_contract(params={"metrics": ["net_revenue"]}, confidence=0.5)
        ready = complete_structure(semantic, contract, dashboard_contract)
        assert ready.contract_id == "dashboard.sales_overview"
        assert ready.confidence == 0.5
        assert isinstance(ready.capabilities, tuple)
        assert isinstance(ready.param_provenance, dict)
        assert isinstance(ready.completion_warnings, tuple)

    def test_no_ratio_in_any_params(self, dashboard_contract):
        semantic = make_semantic(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        contract = make_contract(params={"metrics": ["net_revenue"]}, confidence=0.5)
        ready = complete_structure(semantic, contract, dashboard_contract)
        all_params = ""
        for rc in ready.capabilities:
            all_params += str(rc.params)
        assert "ratio" not in all_params

    def test_no_churn_in_any_params(self, dashboard_contract):
        semantic = make_semantic(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        contract = make_contract(params={"metrics": ["net_revenue"]}, confidence=0.5)
        ready = complete_structure(semantic, contract, dashboard_contract)
        all_params = ""
        for rc in ready.capabilities:
            all_params += str(rc.params)
        assert "churn" not in all_params

    def test_warnings_for_skipped_capabilities(self, dashboard_contract):
        """Phase 3: explicit modify with missing required fields produces skip warning."""
        semantic = make_semantic(
            params={"metrics": ["net_revenue"]},
            confidence=0.8,
            actions=[{"verb": "modify", "object": "sales", "confidence": 0.8}],
        )
        contract = make_contract(params={"metrics": ["net_revenue"]}, confidence=0.8)
        ready = complete_structure(semantic, contract, dashboard_contract)
        warnings = " ".join(ready.completion_warnings)
        # domain.sales is skipped (missing dimensions)
        assert "domain.sales" in warnings
        assert "skipped" in warnings

    def test_all_capabilities_list(self, dashboard_contract):
        """All 4 capabilities appear in capabilities list (SAFE_SKIP included)."""
        semantic = make_semantic(
            params={"metrics": ["net_revenue"]},
            confidence=0.5,
        )
        contract = make_contract(params={"metrics": ["net_revenue"]}, confidence=0.5)
        ready = complete_structure(semantic, contract, dashboard_contract)
        assert len(ready.capabilities) == 4
        rc = _cap(ready, "domain.sales")
        assert rc.mode == CompletionMode.SAFE_SKIP
        assert rc.params == {}


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

    def test_all_safe_fallbacks_empty(self):
        """No semantic defaults in structural schema — all safe_fallback must be empty."""
        for cap, schema in STRUCTURAL_SCHEMA.items():
            assert schema["safe_fallback"] == {}, (
                f"{cap} has non-empty safe_fallback: {schema['safe_fallback']}. "
                "Structural layer must not contain semantic defaults."
            )

    def test_domain_mode_strict_fail(self):
        for cap, schema in STRUCTURAL_SCHEMA.items():
            if cap.startswith("domain."):
                assert schema["mode"] == CompletionMode.STRICT_FAIL, f"{cap} should be STRICT_FAIL"


# ═══════════════════════════════════════════════════════════════════
# Test: Pipeline invariants (ARCHITECTURAL)
# ═══════════════════════════════════════════════════════════════════

class TestPipelineInvariants:
    """Single test that validates all architectural invariants end-to-end."""

    def test_full_pipeline_invariants(self, dashboard_contract):
        from unittest.mock import patch

        semantic = make_semantic(
            params={"metrics": ["net_revenue"], "time_granularity": "monthly"},
            confidence=0.8,
            actions=[
                {"verb": "modify", "object": "kpi", "confidence": 0.8},
                {"verb": "modify", "object": "timeseries", "confidence": 0.8},
            ],
        )
        contract = make_contract(
            params={"metrics": ["net_revenue"], "timeseries_metric": "net_revenue"},
            confidence=0.8,
        )

        # ── 1. Semantic layer correctness ──
        assert semantic.semantic_params["metrics"] == ["net_revenue"]
        assert semantic.semantic_params["time_granularity"] == "monthly"

        # ── 2. StructuralIR ownership integrity ──
        structural_ir = complete_structure(semantic, contract, dashboard_contract)
        assert len(structural_ir.capabilities) == 4
        assert all(isinstance(c.params, dict) for c in structural_ir.capabilities)

        kpi = next(c for c in structural_ir.capabilities if c.name == "presentation.kpi_row")
        assert kpi.params == {"metrics": ["net_revenue"]}
        assert kpi.mode == CompletionMode.SAFE_COMPLETE

        ts = next(c for c in structural_ir.capabilities if c.name == "presentation.timeseries")
        assert ts.params["metric"] == "net_revenue"

        # ── 3. No flattening leak ──
        assert not hasattr(structural_ir, "flat_params")

        # ── 4a. UI IR purity: run via new path, verify node data matches capability params ──
        from app.graphir.pipeline import GraphIRPipeline

        graph, _ = GraphIRPipeline.run_from_structural(structural_ir)

        kpi_node = _find_node_by_type(graph, "KpiRow")
        assert kpi_node is not None, "Expected KpiRow node in graph"
        assert kpi_node.data == {"metrics": ("net_revenue",)}

        ts_node = _find_node_by_type(graph, "Timeseries")
        assert ts_node is not None, "Expected Timeseries node in graph"
        assert ts_node.data["metric"] == "net_revenue"

        # ── 5. StructuralCoverageValidator integrity ──
        from app.graphir.structural_coverage import StructuralCoverageValidator
        sreport = StructuralCoverageValidator.validate(structural_ir, dashboard_contract)
        assert sreport.is_valid
        assert sreport.completeness > 0
        # Phase 3: no action targets domain.sales or layout.page → both are KEEP → SAFE_SKIP
        assert sreport.safe_skip_count == 2

        # ── 6. Structural determinism ──
        for cap in structural_ir.capabilities:
            if cap.mode == CompletionMode.SAFE_COMPLETE:
                schema = STRUCTURAL_SCHEMA.get(cap.name, {})
                required = schema.get("required", [])
                if required:
                    assert all(
                        r in cap.params for r in required
                    ), f"{cap.name}: missing required field in SAFE_COMPLETE params"


# ═══════════════════════════════════════════════════════════════════
# Tests: Action matching & lifecycle (Phase 2–3)
# ═══════════════════════════════════════════════════════════════════


class TestActionMatching:
    """_match_actions_to_capabilities — Step A."""

    def test_matches_by_object_keywords(self, dashboard_contract):
        from app.engine.structural_completion import _match_actions_to_capabilities
        caps = ["presentation.kpi_row", "presentation.timeseries", "layout.page"]
        result = _match_actions_to_capabilities(
            [{"verb": "modify", "object": "kpi"}], caps, dashboard_contract,
        )
        assert result == {"presentation.kpi_row": "modify"}

    def test_matches_by_template_key(self, dashboard_contract):
        from app.engine.structural_completion import _match_actions_to_capabilities
        caps = ["presentation.kpi_row", "presentation.timeseries", "layout.page"]
        result = _match_actions_to_capabilities(
            [{"verb": "create", "object": "dashboard"}], caps, dashboard_contract,
        )
        assert result == {"layout.page": "create"}

    def test_matches_multiple_actions(self, dashboard_contract):
        from app.engine.structural_completion import _match_actions_to_capabilities
        caps = ["presentation.kpi_row", "presentation.timeseries",
                 "presentation.filter_panel", "layout.page"]
        result = _match_actions_to_capabilities([
            {"verb": "remove", "object": "timeseries"},
            {"verb": "add", "object": "filter"},
        ], caps, dashboard_contract)
        assert result == {
            "presentation.timeseries": "remove",
            "presentation.filter_panel": "add",
        }

    def test_no_match_returns_empty(self, dashboard_contract):
        from app.engine.structural_completion import _match_actions_to_capabilities
        caps = ["presentation.kpi_row", "presentation.timeseries"]
        result = _match_actions_to_capabilities(
            [{"verb": "modify", "object": "unknown"}], caps, dashboard_contract,
        )
        assert result == {}

    def test_empty_actions_returns_empty(self, dashboard_contract):
        from app.engine.structural_completion import _match_actions_to_capabilities
        caps = ["presentation.kpi_row"]
        result = _match_actions_to_capabilities([], caps, dashboard_contract)
        assert result == {}


class TestActionLifecycle:
    """_resolve_action — Step B deterministic rules."""

    def _make_index(self, *caps: str):
        from app.engine.structural_index import StructuralIndex
        from app.engine.state_adapter import ComponentInstanceInfo
        mapping = {
            cap: [ComponentInstanceInfo(capability=cap, path=cap.rsplit(".", 1)[-1])]
            for cap in caps
        }
        return StructuralIndex.from_mapping(mapping)

    def test_create_verb_produces_create(self):
        from app.engine.structural_completion import _resolve_action, CREATE
        assert _resolve_action("presentation.table", "create") == CREATE

    def test_modify_verb_produces_modify(self):
        from app.engine.structural_completion import _resolve_action, MODIFY
        assert _resolve_action("presentation.table", "modify") == MODIFY

    def test_delete_verb_produces_delete(self):
        from app.engine.structural_completion import _resolve_action, DELETE
        assert _resolve_action("presentation.table", "delete") == DELETE

    def test_no_verb_produces_keep(self):
        from app.engine.structural_completion import _resolve_action, KEEP
        assert _resolve_action("presentation.table", None) == KEEP

    def test_add_verb_produces_create(self):
        from app.engine.structural_completion import _resolve_action, CREATE
        assert _resolve_action("presentation.table", "add") == CREATE

    def test_unrecognized_verb_produces_keep(self):
        from app.engine.structural_completion import _resolve_action, KEEP
        assert _resolve_action("presentation.table", "custom_action") == KEEP


class TestCompleteStructureWithActions:
    """complete_structure con actions y structural_index."""

    def _make_index(self, *caps: str):
        from app.engine.structural_index import StructuralIndex
        from app.engine.state_adapter import ComponentInstanceInfo
        mapping = {
            cap: [ComponentInstanceInfo(capability=cap, path=cap.rsplit(".", 1)[-1])]
            for cap in caps
        }
        return StructuralIndex.from_mapping(mapping)

    def test_create_default_when_no_repo(self, dashboard_contract):
        """Phase 3: explicit CREATE actions produce CREATE lifecycle for contract caps."""
        semantic = SemanticResolution(
            semantic_params={"metrics": ["revenue"]},
            semantic_provenance={"metrics": "user_explicit"},
            confidence=0.9,
            actions=[{"verb": "create", "object": "kpi", "confidence": 0.9}],
        )
        contract_res = ContractResolution.from_skillir(
            MockSkillIR({"metrics": ["revenue"]}, "dashboard.sales_overview"), dashboard_contract,
        )
        ir = complete_structure(semantic, contract_res, dashboard_contract)
        assert _cap(ir, "presentation.kpi_row").action == "CREATE"

    def test_keep_capabilities_in_repo(self, dashboard_contract):
        semantic = SemanticResolution(
            semantic_params={},
            semantic_provenance={},
            confidence=0.9,
            actions=[],
        )
        contract_res = ContractResolution.from_skillir(
            MockSkillIR({}, "dashboard.sales_overview"), dashboard_contract,
        )
        repo = {"presentation.kpi_row", "presentation.timeseries", "layout.page"}
        ir = complete_structure(semantic, contract_res, dashboard_contract)
        keeps = [c for c in ir.capabilities if c.action == "KEEP"]
        assert len(keeps) > 0
        for k in keeps:
            # KEEP now populates params from contract defaults for composition
            assert k.mode == CompletionMode.SAFE_SKIP

    def test_modify_action_from_semantic(self, dashboard_contract):
        semantic = SemanticResolution(
            semantic_params={"metrics": ["growth"]},
            semantic_provenance={"metrics": "user_explicit"},
            confidence=0.9,
            actions=[{"verb": "modify", "object": "kpi", "confidence": 0.9}],
        )
        contract_res = ContractResolution.from_skillir(
            MockSkillIR({"metrics": ["growth"]}, "dashboard.sales_overview"), dashboard_contract,
        )
        repo = {"presentation.kpi_row", "presentation.timeseries"}
        ir = complete_structure(semantic, contract_res, dashboard_contract)
        kpi = next(c for c in ir.capabilities if c.name == "presentation.kpi_row")
        assert kpi.action == "MODIFY"
        assert kpi.params.get("metrics") is not None

    def test_delete_action_from_semantic(self, dashboard_contract):
        semantic = SemanticResolution(
            semantic_params={},
            semantic_provenance={},
            confidence=0.9,
            actions=[{"verb": "remove", "object": "timeseries", "confidence": 0.9}],
        )
        contract_res = ContractResolution.from_skillir(
            MockSkillIR({}, "dashboard.sales_overview"), dashboard_contract,
        )
        repo = {"presentation.kpi_row", "presentation.timeseries", "layout.page"}
        ir = complete_structure(semantic, contract_res, dashboard_contract)
        ts = next(c for c in ir.capabilities if c.name == "presentation.timeseries")
        assert ts.action == "DELETE"
        assert ts.params == {}

    def test_repo_only_capability_not_in_lifecycle(self, dashboard_contract):
        """Phase 3: contract-first closed-world.
        A capability in repo but not in the contract does NOT enter lifecycle.
        'remove barchart' → presentation.chart.bar exists in repo
        but dashboard.sales_overview contract doesn't declare it → NO lifecycle."""
        semantic = SemanticResolution(
            semantic_params={},
            semantic_provenance={},
            confidence=0.9,
            actions=[{"verb": "remove", "object": "barchart", "confidence": 0.9}],
        )
        contract_res = ContractResolution.from_skillir(
            MockSkillIR({}, "dashboard.sales_overview"), dashboard_contract,
        )
        repo = {
            "presentation.kpi_row", "presentation.timeseries", "layout.page",
            "presentation.chart.bar",
        }
        ir = complete_structure(semantic, contract_res, dashboard_contract)
        names = {c.name: c.action for c in ir.capabilities}
        assert "presentation.chart.bar" not in names, (
            f"Repo-only cap leaked into lifecycle: {names}"
        )


class TestCompositionSync:
    """3E: Page composition sync — CREATE/DELETE child → parent MODIFY."""

    def _make_index(self, *caps: str):
        from app.engine.structural_index import StructuralIndex
        from app.engine.state_adapter import ComponentInstanceInfo
        mapping = {
            cap: [ComponentInstanceInfo(capability=cap, path=cap.rsplit(".", 1)[-1])]
            for cap in caps
        }
        return StructuralIndex.from_mapping(mapping)

    def test_build_composition_map_dashboard(self, dashboard_contract):
        """dashboard.sales_overview has kpi_row and timeseries as children of layout.page."""
        from app.engine.structural_completion import _build_contract_composition_map
        cmap = _build_contract_composition_map(dashboard_contract)
        assert "presentation.kpi_row" in cmap
        assert "presentation.timeseries" in cmap
        assert cmap["presentation.kpi_row"] == "layout.page"
        assert cmap["presentation.timeseries"] == "layout.page"

    def test_build_composition_map_no_page_contract(self, table_contract):
        """analytics.table has no Page capability → empty map."""
        from app.engine.structural_completion import _build_contract_composition_map
        cmap = _build_contract_composition_map(table_contract)
        assert cmap == {}

    def test_create_child_promotes_parent_to_modify(self, dashboard_contract):
        """Add trend chart → CREATE timeseries → layout.page must be MODIFY."""
        semantic = SemanticResolution(
            semantic_params={"metric": "revenue"},
            semantic_provenance={"metric": "user_explicit"},
            confidence=0.9,
            actions=[{"verb": "create", "object": "timeseries", "confidence": 0.9}],
        )
        contract_res = ContractResolution.from_skillir(
            MockSkillIR({"metric": "revenue"}, "dashboard.sales_overview"), dashboard_contract,
        )
        repo = {"layout.page", "presentation.kpi_row"}
        ir = complete_structure(semantic, contract_res, dashboard_contract)
        actions = {c.name: c.action for c in ir.capabilities}
        assert actions.get("presentation.timeseries") == "CREATE", str(actions)
        assert actions.get("layout.page") == "MODIFY", (
            f"layout.page should be MODIFY when child is CREATE, got {actions.get('layout.page')}"
        )

    def test_delete_child_promotes_parent_to_modify(self, dashboard_contract):
        """Remove KPI row → DELETE kpi_row → layout.page must be MODIFY."""
        semantic = SemanticResolution(
            semantic_params={},
            semantic_provenance={},
            confidence=0.9,
            actions=[{"verb": "remove", "object": "kpi", "confidence": 0.9}],
        )
        contract_res = ContractResolution.from_skillir(
            MockSkillIR({}, "dashboard.sales_overview"), dashboard_contract,
        )
        repo = {"layout.page", "presentation.kpi_row", "presentation.timeseries"}
        ir = complete_structure(semantic, contract_res, dashboard_contract)
        actions = {c.name: c.action for c in ir.capabilities}
        assert actions.get("presentation.kpi_row") == "DELETE", str(actions)
        assert actions.get("layout.page") == "MODIFY", (
            f"layout.page should be MODIFY when child is DELETE, got {actions.get('layout.page')}"
        )

    def test_modify_child_does_not_promote_parent(self, dashboard_contract):
        """Update KPI metrics → MODIFY kpi_row → layout.page stays KEEP."""
        semantic = SemanticResolution(
            semantic_params={"metrics": ["revenue"]},
            semantic_provenance={"metrics": "user_explicit"},
            confidence=0.9,
            actions=[{"verb": "modify", "object": "kpi", "confidence": 0.9}],
        )
        contract_res = ContractResolution.from_skillir(
            MockSkillIR({"metrics": ["revenue"]}, "dashboard.sales_overview"), dashboard_contract,
        )
        repo = {"layout.page", "presentation.kpi_row", "presentation.timeseries"}
        ir = complete_structure(semantic, contract_res, dashboard_contract)
        actions = {c.name: c.action for c in ir.capabilities}
        assert actions.get("presentation.kpi_row") == "MODIFY", str(actions)
        # MODIFY on child should NOT promote parent — no composition change needed
        assert actions.get("layout.page") in ("KEEP",), (
            f"layout.page should stay KEEP on child MODIFY, got {actions.get('layout.page')}"
        )

    def test_no_composition_map_contract(self, table_contract):
        """analytics.table has no composition map → CREATE doesn't touch parent."""
        semantic = SemanticResolution(
            semantic_params={"columns": ["A", "B"]},
            semantic_provenance={"columns": "user_explicit"},
            confidence=0.9,
            actions=[{"verb": "create", "object": "table", "confidence": 0.9}],
        )
        contract_res = ContractResolution.from_skillir(
            MockSkillIR({"columns": ["A", "B"]}, "analytics.table"), table_contract,
        )
        ir = complete_structure(semantic, contract_res, table_contract)
        # Only presentation.table should exist (no page, no composition)
        caps = [c for c in ir.capabilities if c.action != "KEEP"]
        # The key assertion: no parent capability gets promoted spuriously
        page_caps = [c for c in ir.capabilities if "layout." in c.name or "page" in c.name]
        assert len(page_caps) == 0, (
            f"No page capability should exist for analytics.table: {[c.name for c in page_caps]}"
        )
        assert any(c.name == "presentation.table" for c in ir.capabilities)

    def test_parent_already_modify_no_duplicate(self, dashboard_contract):
        """When parent is already MODIFY, composition sync doesn't double-promote."""
        semantic = SemanticResolution(
            semantic_params={"metric": "revenue"},
            semantic_provenance={"metric": "user_explicit"},
            confidence=0.9,
            actions=[
                {"verb": "modify", "object": "dashboard", "confidence": 0.9},
                {"verb": "create", "object": "timeseries", "confidence": 0.9},
            ],
        )
        contract_res = ContractResolution.from_skillir(
            MockSkillIR({"metric": "revenue"}, "dashboard.sales_overview"), dashboard_contract,
        )
        repo = {"layout.page", "presentation.kpi_row"}
        ir = complete_structure(semantic, contract_res, dashboard_contract)
        actions = {c.name: c.action for c in ir.capabilities}
        # layout.page should be MODIFY (from "modify dashboard")
        assert actions.get("layout.page") == "MODIFY", str(actions)
        # timeseries should be CREATE (from "create timeseries")
        assert actions.get("presentation.timeseries") == "CREATE", str(actions)
        # Verify layout.page appears exactly once in operations
        ops = ir.operations
        page_ops = [o for o in ops if o["target"] == "layout.page"]
        assert len(page_ops) == 1, f"Expected exactly 1 layout.page op, got {len(page_ops)}: {page_ops}"

    def test_composition_sync_warning(self, dashboard_contract):
        """Composition sync should emit a warning in completion_warnings."""
        semantic = SemanticResolution(
            semantic_params={"metric": "revenue"},
            semantic_provenance={"metric": "user_explicit"},
            confidence=0.9,
            actions=[{"verb": "create", "object": "timeseries", "confidence": 0.9}],
        )
        contract_res = ContractResolution.from_skillir(
            MockSkillIR({"metric": "revenue"}, "dashboard.sales_overview"), dashboard_contract,
        )
        repo = {"layout.page", "presentation.kpi_row"}
        ir = complete_structure(semantic, contract_res, dashboard_contract)
        sync_warnings = [w for w in ir.completion_warnings if "composition sync" in w]
        assert len(sync_warnings) >= 1, (
            f"Expected composition sync warning, got: {ir.completion_warnings}"
        )
        assert "layout.page" in sync_warnings[0]


class MockSkillIR:
    """Minimal SkillIR-like object for test compatibility."""
    def __init__(self, params, contract_id, confidence=1.0):
        self.params = params
        self.contract_id = contract_id
        self.version = 1
        self.confidence = confidence
