"""Tests for StructuralCoverageValidator."""
from __future__ import annotations

import pytest

from app.engine.structural_completion import (
    CompletionMode,
    StructuralIR,
    ResolvedCapability,
)
from app.graphir.structural_coverage import (
    StructuralCoverageValidator,
    StructuralCoverageReport,
    StructuralIntegrityError,
)
from app.contracts.skill_registry import SkillContract


def make_resolution(contract_id="dashboard.sales_overview", params=None, provenance=None, confidence=0.8):
    """Minimal SemanticResolution factory for test isolation."""
    from app.contracts.semantic_resolution import SemanticResolution
    return SemanticResolution(
        contract_id=contract_id,
        contract_version=1,
        params=params or {},
        param_provenance=provenance or {},
        confidence=confidence,
    )


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


class TestStructuralCoverageValidator:
    """StructuralCoverageValidator reemplaza a IntentCoverageValidator en NEW PATH.

    NO intents. NO keywords. NO decomposition confidence.
    Solo coherencia estructural y compliance de contrato.
    """

    def test_safe_complete_valid_passes(self):
        """SAFE_COMPLETE con todos los required fields → OK."""
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=[
                ResolvedCapability(
                    name="presentation.kpi_row",
                    params={"metrics": ["net_revenue"]},
                    mode=CompletionMode.SAFE_COMPLETE,
                ),
            ],
            param_provenance={}, confidence=1.0,
        )
        report = StructuralCoverageValidator.validate(ir)
        assert report.is_valid
        assert report.completeness == 1.0
        assert report.safe_skip_count == 0
        assert len(report.warnings) == 0

    def test_safe_complete_with_fallback_passes(self):
        """SAFE_COMPLETE con required missing pero fallback definido → WARNING, no RAISE."""
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=[
                ResolvedCapability(
                    name="presentation.timeseries",
                    params={},  # metric missing, but safe_fallback exists
                    mode=CompletionMode.SAFE_COMPLETE,
                ),
            ],
            param_provenance={}, confidence=1.0,
        )
        report = StructuralCoverageValidator.validate(ir)
        assert report.is_valid
        assert len(report.warnings) == 1
        assert "timeseries" in report.warnings[0]
        assert "fallback" in report.warnings[0]

    def test_safe_skip_passes_with_warning(self):
        """SAFE_SKIP por falta semántica → WARNING, no RAISE."""
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=[
                ResolvedCapability(
                    name="domain.sales",
                    params={},
                    mode=CompletionMode.SAFE_SKIP,
                ),
                ResolvedCapability(
                    name="layout.page",
                    params={},
                    mode=CompletionMode.SAFE_COMPLETE,
                ),
            ],
            param_provenance={}, confidence=0.5,
        )
        report = StructuralCoverageValidator.validate(ir)
        assert report.is_valid
        assert report.safe_skip_count == 1
        assert report.completeness == 0.5

    def test_ghost_capability_raises(self):
        """Ghost capability (no en STRUCTURAL_SCHEMA) → RAISE StructuralIntegrityError."""
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=[
                ResolvedCapability(
                    name="ghost.invalid",
                    params={},
                    mode=CompletionMode.SAFE_COMPLETE,
                ),
            ],
            param_provenance={}, confidence=1.0,
        )
        with pytest.raises(StructuralIntegrityError) as exc:
            StructuralCoverageValidator.validate(ir)
        assert "ghost.invalid" in str(exc.value)

    def test_missing_required_no_fallback_raises(self):
        """SAFE_COMPLETE con required field faltante y sin fallback → RAISE."""
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=[
                ResolvedCapability(
                    name="presentation.embed",
                    params={},  # 'src' is required, no fallback in schema
                    mode=CompletionMode.SAFE_COMPLETE,
                ),
            ],
            param_provenance={}, confidence=1.0,
        )
        with pytest.raises(StructuralIntegrityError) as exc:
            StructuralCoverageValidator.validate(ir)
        assert "src" in str(exc.value)

    def test_safe_skip_no_missing_required_warns(self):
        """SAFE_SKIP sin required fields faltantes → WARNING (skip innecesario)."""
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=[
                ResolvedCapability(
                    name="layout.page",
                    params={},  # no required fields
                    mode=CompletionMode.SAFE_SKIP,  # unnecessary skip
                ),
            ],
            param_provenance={}, confidence=1.0,
        )
        report = StructuralCoverageValidator.validate(ir)
        assert report.is_valid
        assert len(report.warnings) == 1
        assert "unnecessary skip" in report.warnings[0]

    def test_known_capabilities_override(self):
        """known_capabilities permite capabilities que no están en STRUCTURAL_SCHEMA."""
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=[
                ResolvedCapability(
                    name="custom.capability",
                    params={},
                    mode=CompletionMode.SAFE_SKIP,
                ),
            ],
            param_provenance={}, confidence=1.0,
        )
        report = StructuralCoverageValidator.validate(
            ir, known_capabilities={"custom.capability"},
        )
        assert report.is_valid

    def test_contract_capabilities_allowed(self):
        """Contract capabilities se pasan al validador y no se marcan como ghost."""
        contract = SkillContract(
            contract_id="test", version=1,
            input_schema={},
            ast_template={"capabilities": {"Page": "layout.page"}},
            renderer={},
        )
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=[
                ResolvedCapability(
                    name="layout.page",
                    params={},
                    mode=CompletionMode.SAFE_COMPLETE,
                ),
            ],
            param_provenance={}, confidence=1.0,
        )
        report = StructuralCoverageValidator.validate(ir, contract=contract)
        assert report.is_valid
        assert report.completeness == 1.0

    def test_empty_capabilities(self):
        """StructuralIR sin capabilities → completeness=1.0, is_valid."""
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=[],
            param_provenance={}, confidence=1.0,
        )
        report = StructuralCoverageValidator.validate(ir)
        assert report.is_valid
        assert report.completeness == 1.0
        assert report.safe_skip_count == 0


class TestPipelineInvariantsExtended:
    """Extended invariants: StructuralCoverageValidator path has no intent leaks."""

    def test_no_intents_in_structural_path(self, dashboard_contract):
        """Structural path no usa decomposition intents ni IntentCoverageValidator."""
        from unittest.mock import patch
        from app.engine.structural_completion import complete_structure
        from app.graphir.structural_coverage import StructuralCoverageValidator
        from app.graphir.pipeline import GraphIRPipeline
        from app.graphir import intent_coverage as ic_module
        from app.graphir import binding as binding_module
        from app.graphir.builder import GraphIRBuilder

        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.8,
        )
        structural_ir = complete_structure(resolution, dashboard_contract)

        # Validate coverage
        sreport = StructuralCoverageValidator.validate(structural_ir, dashboard_contract)
        assert sreport.is_valid

        # Execute full new path with mocks on forbidden functions
        with (
            patch.object(ic_module.IntentCoverageValidator, "check_coverage") as mock_cc,
            patch.object(ic_module.IntentCoverageValidator, "revalidate") as mock_reval,
            patch.object(binding_module, "bind_skillir_to_nodes") as mock_bind,
            patch.object(binding_module, "validate_binding") as mock_val,
            patch.object(GraphIRBuilder, "build") as mock_legacy_build,
        ):
            graph, layout = GraphIRPipeline.run_from_structural(structural_ir)

            # These should NEVER be called in the new path
            mock_cc.assert_not_called()
            mock_reval.assert_not_called()
            mock_bind.assert_not_called()
            mock_val.assert_not_called()
            mock_legacy_build.assert_not_called()

        # Verify output is purely structural
        assert graph.nodes is not None
        assert "KpiRow_1" in graph.nodes
        assert graph.nodes["KpiRow_1"].data == {"metrics": ("net_revenue",)}

    def test_no_intent_plan_created_in_structural_path(self, dashboard_contract):
        """Structural path NO crea IntentPlan — usa StructuralIR directamente."""
        from app.engine.structural_completion import complete_structure, graphir_ready_to_intent_plan
        from app.graphir.structural_coverage import StructuralCoverageValidator
        from app.graphir.pipeline import GraphIRPipeline

        resolution = make_resolution(
            params={"metrics": ["net_revenue"]},
            confidence=0.8,
        )
        structural_ir = complete_structure(resolution, dashboard_contract)

        # The new path should NOT call graphir_ready_to_intent_plan
        # IntentPlan is only for legacy compat — verify the new path works without it
        sreport = StructuralCoverageValidator.validate(structural_ir, dashboard_contract)
        graph, layout = GraphIRPipeline.run_from_structural(structural_ir)

        # Verify node data comes from structural_ir, not an intermediate IntentPlan
        kpi = graph.nodes.get("KpiRow_1")
        assert kpi is not None
        assert dict(kpi.data) == {"metrics": ("net_revenue",)}

        # graphir_ready_to_intent_plan still exists for legacy, verify it produces empty params
        plan = graphir_ready_to_intent_plan(structural_ir)
        assert plan.params == {}
        assert all(i.source == "structural_completion" for i in plan.intents)

