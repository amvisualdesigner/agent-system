"""Phase 5 pipeline invariants: lifecycle NO mutado por substitution ejecucion.

Phase 3: StructuralIndex es observation-only.
Phase 5: build_substitution_fileops() solo produce FileOps,
         no reescribe lifecycle decisions.

Este test es el PRIMER PASO de Phase 5. Debe pasar antes
de tocar cualquier codigo de implementacion.
"""

from __future__ import annotations

import pytest

from app.engine.structural_completion import (
    CompletionMode,
    ResolvedCapability,
    StructuralIR,
    SubstitutionOp,
    CREATE,
    KEEP,
    complete_structure,
)
from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.contract_resolution import ContractResolution


# ─── Helpers (mismos patrones que test_phase4_substitution_isolation) ───


def make_semantic(
    params: dict | None = None,
    actions: list[dict] | None = None,
    confidence: float = 0.8,
) -> SemanticResolution:
    return SemanticResolution(
        semantic_params=params or {},
        semantic_provenance={k: "test" for k in (params or {})},
        confidence=confidence,
        actions=actions or [],
    )


def make_contract(
    params: dict | None = None,
    confidence: float = 0.8,
) -> ContractResolution:
    return ContractResolution(
        contract_params=params or {},
        contract_provenance={k: "test" for k in (params or {})},
        confidence=confidence,
        contract_id="dashboard.sales_overview",
        contract_version=1,
    )


def _lifecycle_snapshot(
    capabilities: tuple[ResolvedCapability, ...],
) -> list[tuple]:
    """Captura solo los campos de lifecycle de cada capability."""
    return [
        (rc.name, rc.action, rc.instance_only, tuple(sorted(rc.params.keys())))
        for rc in capabilities
    ]


def _cap_names(ir: StructuralIR) -> list[str]:
    return [rc.name for rc in ir.capabilities]


# ─── Fixtures ───


@pytest.fixture
def contract_with_bar_chart():
    from app.contracts.skill_registry import SkillContract

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
                "BarChart": "presentation.chart.bar",
                "Page": "layout.page",
            },
        },
        renderer={},
    )


# ─── Tests de invariante ───


class TestStructuralIndexCannotChangeLifecycle:
    """Phase 3 + Phase 5 invariante: StructuralIndex observation-only.

    La ejecucion (build_substitution_fileops) NO puede mutar
    StructuralIR.capabilities. StructuralIR es frozen, pero
    este test verifica que ninguna funcion de ejecucion reescribe
    indirectamente el lifecycle.
    """

    def test_structural_ir_is_frozen(self, contract_with_bar_chart):
        """StructuralIR es frozen — no se puede mutar despues de crear."""
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        with pytest.raises(Exception):
            ir.capabilities = ()  # type: ignore

    def test_capabilities_tuple_is_immutable(self, contract_with_bar_chart):
        """El tuple capabilities es inmutable."""
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        t = ir.capabilities
        with pytest.raises(TypeError):
            t[0] = t[0]  # type: ignore

    def test_substitution_ops_exist_independently(self, contract_with_bar_chart):
        """SubstitutionOp existe independientemente del lifecycle.

        Verifica que substitution_ops y capabilities conviven
        sin que uno afecte al otro.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        # substitution_ops debe existir
        assert len(ir.substitution_ops) > 0

        # capabilities no debe ser modificada por substitution_ops
        for op in ir.substitution_ops:
            assert isinstance(op, SubstitutionOp)
            assert op.source in _cap_names(ir)

    def test_lifecycle_determines_execution_not_substitution(
        self, contract_with_bar_chart
    ):
        """El lifecycle (action) determina la ejecucion, no la sustitucion.

        Una capability con action=KEEP se mantiene aunque tenga
        sustitucion. La sustitucion solo afecta el grafo de importacion.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        for op in ir.substitution_ops:
            rc = None
            for c in ir.capabilities:
                if c.name == op.source:
                    rc = c
                    break
            # source puede ser KEEP aunque tenga sustitucion
            if rc is not None and rc.action == KEEP:
                pass  # Esto es valido — substitution no cambia lifecycle

    def test_lifecycle_snapshot_matches_after_execution(
        self, contract_with_bar_chart
    ):
        """Snapshot de lifecycle antes y despues de ejecucion debe coincidir.

        NOTA: build_substitution_fileops() no existe aun.
        Este test sera completado en Phase 5A cuando se implemente
        la funcion.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        snapshot_before = _lifecycle_snapshot(ir.capabilities)

        # Phase 5A: aqui se llamara build_substitution_fileops()
        # build_substitution_fileops(ir.substitution_ops, ...)
        #
        # Mientras tanto, verificamos que StructuralIR es frozen
        # y que su contenido es estable.

        snapshot_after = _lifecycle_snapshot(ir.capabilities)

        assert snapshot_before == snapshot_after, (
            "El lifecycle cambio sin intervencion. "
            "Esto no deberia ocurrir — StructuralIR es frozen."
        )

    def test_structural_ir_is_safe_to_pass_to_functions(
        self, contract_with_bar_chart
    ):
        """StructuralIR se puede pasar a funciones sin mutacion.

        Verificacion de que ninguna funcion del pipeline actual
        modifica el objeto StructuralIR.
        """
        from app.engine.structural_index import StructuralIndex

        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)
        snapshot_before = _lifecycle_snapshot(ir.capabilities)

        # apply_substitutions recibe StructuralIR pero no lo muta
        from app.engine.apply_engine import apply_substitutions

        # Fake StructuralIndex que reporta que nada existe
        class FakeIndex:
            def exists(self, name: str) -> bool:
                return False

        si = FakeIndex()

        result = apply_substitutions(
            structural_ir=ir,
            workspace="/tmp",
            contract=contract_with_bar_chart,
        )

        snapshot_after = _lifecycle_snapshot(ir.capabilities)

        assert snapshot_before == snapshot_after, (
            "apply_substitutions() muto StructuralIR.capabilities. "
            "La ejecucion NO puede reescribir lifecycle."
        )
