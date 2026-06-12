"""Phase 5C unit tests: RefactorChange trazabilidad."""
from __future__ import annotations

import pytest

from app.intent.models import RefactorChange
from app.engine.structural_completion import (
    StructuralIR,
    ResolvedCapability,
    SubstitutionOp,
    CREATE,
    KEEP,
    MODIFY,
    DELETE,
    complete_structure,
)
from app.contracts.skill_registry import SkillContract


# ─── Fixtures ───


@pytest.fixture
def contract_with_composition():
    return SkillContract(
        contract_id="dashboard.sales_overview",
        version=1,
        input_schema={
            "type": "object",
            "properties": {
                "metrics": {"type": "array", "items": {"type": "string"}},
            },
        },
        ast_template={
            "capabilities": {
                "KpiRow": "presentation.kpi_row",
                "Page": "layout.page",
                "BarChart": "presentation.chart.bar",
            },
        },
        renderer={
            "base_path": "frontend/src",
            "files": [
                {"path": "components/dashboard/KpiRow.tsx", "type": "component"},
                {"path": "components/charts/BarChart.tsx", "type": "component"},
                {"path": "pages/dashboard/Page.tsx", "type": "page"},
            ],
        },
    )


# ─── Helpers ───


def make_semantic(
    params: dict | None = None,
    actions: list[dict] | None = None,
    confidence: float = 0.8,
):
    from app.contracts.semantic_resolution import SemanticResolution
    return SemanticResolution(
        semantic_params=params or {},
        semantic_provenance={k: "test" for k in (params or {})},
        confidence=confidence,
        actions=actions or [],
    )


def make_contract_resolution(
    params: dict | None = None,
    confidence: float = 0.8,
):
    from app.contracts.contract_resolution import ContractResolution
    return ContractResolution(
        contract_params=params or {},
        contract_provenance={k: "test" for k in (params or {})},
        confidence=confidence,
        contract_id="dashboard.sales_overview",
        contract_version=1,
    )


# ─── Tests: RefactorChange dataclass ───


class TestRefactorChangeModel:
    """RefactorChange is a simple dataclass with 5 fields."""

    def test_fields(self):
        rc = RefactorChange(
            change_type="import_redirect",
            source="presentation.table",
            target="presentation.chart.bar",
            file_path="components/Dashboard.tsx",
            reason="redirect imports",
        )
        assert rc.change_type == "import_redirect"
        assert rc.source == "presentation.table"
        assert rc.target == "presentation.chart.bar"
        assert rc.file_path == "components/Dashboard.tsx"
        assert rc.reason == "redirect imports"

    def test_file_path_optional(self):
        rc = RefactorChange(
            change_type="composition_sync",
            source="presentation.kpi_row",
            target="layout.page",
            reason="composition sync",
        )
        assert rc.file_path is None

    def test_valid_change_types(self):
        for ct in ("import_redirect", "composition_sync"):
            rc = RefactorChange(change_type=ct, source="a", target="b")
            assert rc.change_type == ct


# ─── Tests: composition_sync_trace in StructuralIR ───


class TestCompositionSyncTrace:
    """composition_sync_trace se genera en complete_structure."""

    def test_trace_is_present(self, contract_with_composition):
        sem = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        cr = make_contract_resolution(params={"metrics": ["revenue"]})

        ir = complete_structure(sem, cr, contract_with_composition)

        assert hasattr(ir, "composition_sync_trace")
        assert isinstance(ir.composition_sync_trace, tuple)

    def test_trace_contains_refactor_change_instances(self, contract_with_composition):
        sem = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        cr = make_contract_resolution(params={"metrics": ["revenue"]})

        ir = complete_structure(sem, cr, contract_with_composition)

        for rc in ir.composition_sync_trace:
            assert isinstance(rc, RefactorChange)

    def test_composition_sync_change_type(self, contract_with_composition):
        sem = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        cr = make_contract_resolution(params={"metrics": ["revenue"]})

        ir = complete_structure(sem, cr, contract_with_composition)

        for rc in ir.composition_sync_trace:
            assert rc.change_type == "composition_sync"

    def test_trace_is_frozen(self, contract_with_composition):
        sem = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        cr = make_contract_resolution(params={"metrics": ["revenue"]})

        ir = complete_structure(sem, cr, contract_with_composition)

        with pytest.raises(Exception):
            ir.composition_sync_trace = ()  # type: ignore


# ─── Tests: refactor_changes in build_substitution_fileops ───


class TestRefactorChangesFromSubstitution:
    """build_substitution_fileops produces RefactorChange entries."""

    def test_import_redirect_produces_refactor(self, contract_with_composition):
        from app.engine.apply_engine import build_substitution_fileops
        from app.engine.structural_completion import SubstitutionOp

        class FakeIndex:
            def exists(self, capability):
                return False

        ops = (SubstitutionOp(source="presentation.kpi_row", target="presentation.kpi_row"),)
        fileops, refactor_changes = build_substitution_fileops(ops, FakeIndex(), "/tmp", contract_with_composition)

        assert len(refactor_changes) > 0
        for rc in refactor_changes:
            assert rc.change_type == "import_redirect"

    def test_refactor_count_matches_fileops(self, contract_with_composition):
        from app.engine.apply_engine import build_substitution_fileops
        from app.engine.structural_completion import SubstitutionOp

        class FakeIndex:
            def exists(self, capability):
                return True

        ops = (SubstitutionOp(source="presentation.kpi_row", target="presentation.kpi_row"),)
        fileops, refactor_changes = build_substitution_fileops(ops, FakeIndex(), "/tmp", contract_with_composition)

        # Each fileop should have corresponding refactor change
        assert len(refactor_changes) >= len(fileops)

    def test_refactor_has_source_target(self, contract_with_composition):
        from app.engine.apply_engine import build_substitution_fileops
        from app.engine.structural_completion import SubstitutionOp

        class FakeIndex:
            def exists(self, capability):
                return False

        ops = (SubstitutionOp(source="presentation.table", target="presentation.kpi_row"),)
        fileops, refactor_changes = build_substitution_fileops(ops, FakeIndex(), "/tmp", contract_with_composition)

        for rc in refactor_changes:
            assert "presentation" in rc.source or "presentation" in rc.target

    def test_composition_sync_trace_in_structural_ir(self, contract_with_composition):
        """composition_sync_trace from complete_structure is a tuple of RefactorChange."""
        sem = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        cr = make_contract_resolution(params={"metrics": ["revenue"]})

        ir = complete_structure(sem, cr, contract_with_composition)

        assert isinstance(ir.composition_sync_trace, tuple)
        for rc in ir.composition_sync_trace:
            assert rc.change_type == "composition_sync"
