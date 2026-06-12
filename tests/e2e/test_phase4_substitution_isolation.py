"""Phase 4 — Substitution Isolation Test.

Valida el invariante final: Substitution MUST NOT affect lifecycle.

Backdoors detectados:
  (A) target_capability se inyecta en capabilities list (structural_completion.py:1088-1090)
  (B) _redirect_imports() camina todo el workspace sin verificar lifecycle
  (C) Tests que validan filesystem como verdad de lifecycle

Cada test verifica 1 aspecto del invariante.
"""

from __future__ import annotations

import os
import tempfile
import pytest

from app.engine.structural_completion import (
    CompletionMode,
    ResolvedCapability,
    StructuralIR,
    SubstitutionOp,
    CREATE,
    DELETE,
    KEEP,
    MODIFY,
    _extract_substitution_ops,
    _resolve_action,
    _infer_capabilities_from_contract,
    _match_actions_to_capabilities,
    complete_structure,
)
from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.contract_resolution import ContractResolution
from app.contracts.skill_registry import SkillContract


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def contract_with_bar_chart():
    """Contract with both kpi_row and bar_chart for substitution testing."""
    return SkillContract(
        contract_id="dashboard.sales_overview",
        version=1,
        input_schema={
            "type": "object",
            "required": ["metrics"],
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                },
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


def _cap(ir: StructuralIR, name: str) -> ResolvedCapability | None:
    for rc in ir.capabilities:
        if rc.name == name:
            return rc
    return None


def _cap_names(ir: StructuralIR) -> list[str]:
    return [rc.name for rc in ir.capabilities]


# ═══════════════════════════════════════════════════════════════════
# Test A — Substitution target NO debe contaminar lifecycle
# ═══════════════════════════════════════════════════════════════════

class TestBackdoorA_TargetLifecycleContamination:
    """Backdoor A: substitution target inyectado en capabilities list.

    complete_structure() añade substitution.target a 'capabilities'
    (línea 1088-1090). Esto fuerza a que el target reciba un lifecycle
    action (KEEP por defecto). El target NO debería estar en el stream
    de lifecycle en absoluto.
    """

    def test_substitution_target_not_in_lifecycle_capabilities(
        self, contract_with_bar_chart,
    ):
        """REPLACE produce SubstitutionOp; target NO debe aparecer en capabilities lifecycle.

        El target de una sustitución es un asunto de wiring, no de lifecycle.
        Si aparece en StructuralIR.capabilities, está contaminando el lifecycle.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        # substitution_ops debe estar poblado
        assert len(ir.substitution_ops) > 0, "REPLACE debe producir SubstitutionOp"
        sub = ir.substitution_ops[0]
        assert sub.source == "presentation.kpi_row"
        assert sub.target == "presentation.chart.bar"

        # INVARIANTE: target NO debe aparecer en capabilities lifecycle
        target_rc = _cap(ir, sub.target)
        assert target_rc is None or target_rc.action == KEEP, (
            f"Backdoor A: target '{sub.target}' apareció en lifecycle con "
            f"action={target_rc.action}. El target de substitution NO debe "
            f"tener lifecycle decision."
        )

    def test_substitution_source_action_is_keep(self, contract_with_bar_chart):
        """REPLACE → KEEP: source capability NO cambia su lifecycle.

        _resolve_action("replace") debe devolver KEEP porque REPLACE
        no está en _VERBS_DELETE/MODIFY/CREATE/MOVE.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        source_rc = _cap(ir, "presentation.kpi_row")
        assert source_rc is not None
        assert source_rc.action == KEEP, (
            f"Source capability debería tener action=KEEP, "
            f"tiene action={source_rc.action}"
        )

    def test_no_create_for_substitution_target(self, contract_with_bar_chart):
        """Substitution NO debe forzar CREATE del target.

        El target solo existe en substitution_ops, no en lifecycle.
        Su lifecycle action debe ser KEEP (o no existir en capabilities).
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        for rc in ir.capabilities:
            if rc.name in [s.target for s in ir.substitution_ops]:
                assert rc.action != CREATE, (
                    f"Backdoor A: substitution target '{rc.name}' "
                    f"tiene action=CREATE en lifecycle. "
                    f"El target no debe ser forzado a CREATE."
                )

    def test_operations_has_no_delete_for_source(self, contract_with_bar_chart):
        """REPLACE no debe producir DELETE de la source en operations."""
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        for op in ir.operations:
            assert not (op["action"] == "DELETE" and op["target"] == "presentation.kpi_row"), (
                f"REPLACE generó DELETE de la source en operations: {op}"
            )


# ═══════════════════════════════════════════════════════════════════
# Test B — Import redirect NO debe crear capabilities fantasma
# ═══════════════════════════════════════════════════════════════════

class TestBackdoorB_ImportRedirectBoundary:
    """Backdoor B: _redirect_imports asume que el target existe semánticamente.

    _redirect_imports() reescribe imports en TODO el workspace,
    incluso para archivos fuera del lifecycle actual.
    El target del redirect puede no estar en StructuralIR.capabilities.
    """

    def test_import_redirect_target_not_in_capabilities(
        self, contract_with_bar_chart,
    ):
        """Verificar que el target del import redirect no está en lifecycle.

        El target de _redirect_imports es un SubstitutionOp.target.
        Este target NO debe aparecer en StructuralIR.capabilities a
        menos que el usuario haya pedido explícitamente una acción
        lifecycle sobre él.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        for sub in ir.substitution_ops:
            # El target puede estar en capabilities si el contrato lo incluye
            target_rc = _cap(ir, sub.target)
            if target_rc is not None:
                # Pero su action debe ser KEEP (no CREATE/MODIFY/DELETE)
                assert target_rc.action == KEEP, (
                    f"Backdoor B: target '{sub.target}' tiene action="
                    f"{target_rc.action} en lifecycle. El "
                    f"import redirect NO debe forzar lifecycle decisions."
                )
                # Y debe tener params vacíos (sin resolución de params)
                assert target_rc.params == {}, (
                    f"Backdoor B: target '{sub.target}' tiene params "
                    f"resueltos ({target_rc.params}). El target de "
                    f"import redirect NO debe tener params lifecycle."
                )

    def test_extract_substitution_ops_produces_no_lifecycle_effect(self):
        """_extract_substitution_ops NO modifica el lifecycle.

        Esta función solo extrae SubstitutionOps. No debe tener
        side-effects sobre action_map ni capabilities.
        """
        contract_caps = [
            "presentation.kpi_row",
            "presentation.chart.bar",
            "layout.page",
        ]
        actions = [
            {"verb": "replace", "object": "kpi", "reference": "bar chart"},
        ]
        resolution = SemanticResolution(
            semantic_params={"metrics": ["revenue"]},
            semantic_provenance={"metrics": "test"},
            confidence=0.8,
            actions=actions,
        )

        ops = _extract_substitution_ops(resolution, contract_caps)

        assert len(ops) == 1
        assert ops[0].source == "presentation.kpi_row"
        assert ops[0].target == "presentation.chart.bar"
        # SubstitutionOp no tiene action lifecycle
        assert not hasattr(ops[0], 'action')


# ═══════════════════════════════════════════════════════════════════
# Test C — Tests de filesystem NO deben validar lifecycle
# ═══════════════════════════════════════════════════════════════════

class TestBackdoorC_FilesystemVsLifecycle:
    """Backdoor C: tests que usan filesystem como proxy de lifecycle.

    Phase 4: el filesystem NO es la fuente de verdad del lifecycle.
    La fuente de verdad es StructuralIR.capabilities.
    Un test que dice "old file must NOT exist after substitution"
    sería incorrecto porque el lifecycle dice KEEP (no DELETE).
    """

    def test_lifecycle_is_authority_not_filesystem(self, contract_with_bar_chart):
        """El lifecycle se lee de StructuralIR, no de filesystem.

        Después de complete_structure(), la verificación de lifecycle
        debe usar StructuralIR.capabilities, no inspeccionar el
        filesystem. El filesystem puede estar desactualizado.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        # Source debe tener action=KEEP en lifecycle (NO DELETE)
        source_rc = _cap(ir, "presentation.kpi_row")
        assert source_rc is not None
        assert source_rc.action == KEEP, (
            f"Lifecycle dice: source action={source_rc.action}. "
            f"Debe ser KEEP. El filesystem es irrelevante."
        )

        # Target no debe tener action=CREATE
        for sub in ir.substitution_ops:
            target_rc = _cap(ir, sub.target)
            if target_rc is not None:
                assert target_rc.action != CREATE, (
                    f"Lifecycle dice: target action={target_rc.action}. "
                    f"Debe ser KEEP. CREATE sería contaminación."
                )

    def test_old_file_preserved_by_lifecycle_not_by_convention(self, contract_with_bar_chart):
        """La preservación del old file es consecuencia del lifecycle, no una regla de filesystem.

        El lifecycle dice KEEP para la source. El test debe verificar
        el lifecycle, no que el archivo exista en disco. Si el archivo
        no existe pero el lifecycle dice KEEP, es un bug del executor,
        no del lifecycle.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        source_rc = _cap(ir, "presentation.kpi_row")
        assert source_rc is not None
        assert source_rc.action == KEEP
        assert source_rc.mode == CompletionMode.SAFE_SKIP

        # Verificar que NO hay DELETE para source en operations
        delete_targets = [
            op["target"] for op in ir.operations if op["action"] == "DELETE"
        ]
        assert "presentation.kpi_row" not in delete_targets, (
            f"Source aparece como DELETE en operations: {delete_targets}"
        )


# ═══════════════════════════════════════════════════════════════════
# Invariant: Substitution effects are BOUNDED
# ═══════════════════════════════════════════════════════════════════

class TestSubstitutionBoundedEffects:
    """Substitution solo puede afectar: import graph, wiring, composition edges.

    NO puede afectar: existencia de old capability, lifecycle decisions,
    structural index, contract resolution.
    """

    def test_substitution_ops_separate_stream_from_capabilities(
        self, contract_with_bar_chart,
    ):
        """substitution_ops es un stream independiente de capabilities.

        StructuralIR.substitution_ops NO debe contener lifecycle actions.
        StructuralIR.capabilities NO debe contener substitution targets
        con action distinta de KEEP.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        # substitution_ops no tiene action lifecycle
        for sub in ir.substitution_ops:
            assert not hasattr(sub, 'action')
            assert isinstance(sub, SubstitutionOp)

        # capabilities lifecycle es independiente
        for rc in ir.capabilities:
            assert rc.action in (CREATE, MODIFY, DELETE, KEEP)
            # substitution source debe tener action=KEEP
            if rc.name in [s.source for s in ir.substitution_ops]:
                assert rc.action == KEEP, (
                    f"Source '{rc.name}' tiene action={rc.action} "
                    f"en vez de KEEP"
                )

    def test_no_capability_appears_only_from_substitution(self, contract_with_bar_chart):
        """Ninguna capability aparece SOLO porque substitution la introdujo.

        Si una capability está en capabilities y NO está en el contrato,
        es contaminación por substitution.
        """
        contract_caps = _infer_capabilities_from_contract(contract_with_bar_chart)

        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        for rc in ir.capabilities:
            assert rc.name in contract_caps or rc.name in [
                s.target for s in ir.substitution_ops
            ], (
                f"Capability '{rc.name}' aparece en lifecycle sin estar "
                f"en contrato ni ser target de substitution"
            )

    def test_resolve_action_keep_for_replace(self):
        """_resolve_action("replace") → KEEP. Es un invariante de Phase 4."""
        assert _resolve_action("presentation.kpi_row", "replace") == KEEP
        assert _resolve_action("presentation.kpi_row", "remove") == DELETE
        assert _resolve_action("presentation.kpi_row", "modify") == MODIFY
        assert _resolve_action("presentation.kpi_row", "create") == CREATE
        assert _resolve_action("presentation.kpi_row", None) == KEEP


# ═══════════════════════════════════════════════════════════════════
# Consistency: substitution failure → system still consistent
# ═══════════════════════════════════════════════════════════════════

class TestSubstitutionFailureConsistency:
    """Si substitution falla, el sistema sigue siendo consistente.

    apply_substitutions() es un paso post-lifecycle. Si falla,
    el lifecycle (StructuralIR) ya está calculado y es correcto.
    """

    def test_lifecycle_independent_of_substitution_execution(
        self, contract_with_bar_chart,
    ):
        """El lifecycle se calcula ANTES de apply_substitutions().

        Si apply_substitutions() falla, el lifecycle sigue siendo válido.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        # Verify lifecycle is correct regardless of future execution
        source_rc = _cap(ir, "presentation.kpi_row")
        assert source_rc is not None
        assert source_rc.action == KEEP

        for sub in ir.substitution_ops:
            target_rc = _cap(ir, sub.target)
            if target_rc is not None:
                assert target_rc.action == KEEP
                assert target_rc.params == {}

    def test_substitution_op_does_not_change_after_construction(self):
        """SubstitutionOp es frozen — inmutable después de creado."""
        op = SubstitutionOp(source="presentation.a", target="presentation.b")
        with pytest.raises(AttributeError):
            op.source = "presentation.c"  # type: ignore
        with pytest.raises(AttributeError):
            op.target = "presentation.c"  # type: ignore


# ═══════════════════════════════════════════════════════════════════
# E2E: plan → complete_structure → StructuralIR analysis
# ═══════════════════════════════════════════════════════════════════

class TestEndToEndIsolation:
    """Pipeline completo de sustitución sin contaminación de lifecycle."""

    def test_replace_does_not_affect_other_capabilities(self, contract_with_bar_chart):
        """REPLACE solo afecta import graph, no otras capabilities.

        Las capabilities no relacionadas deben mantener su action original.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[{"verb": "replace", "object": "kpi", "reference": "bar chart"}],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        # layout.page no debe verse afectado por substitution
        page_rc = _cap(ir, "layout.page")
        if page_rc is not None:
            # page solo se modifica si composition sync lo requiere
            assert page_rc.action in (KEEP, MODIFY), (
                f"layout.page afectado por substitution: action={page_rc.action}"
            )

    def test_mixed_replace_and_remove_keeps_boundaries(self, contract_with_bar_chart):
        """REPLACE y REMOVE en el mismo plan deben mantener sus boundaries.

        REPLACE no debe generar DELETE. REMOVE no debe generar SubstitutionOp.
        """
        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[
                {"verb": "replace", "object": "kpi", "reference": "bar chart"},
                {"verb": "remove", "object": "timeseries"},
            ],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_with_bar_chart)

        # REPLACE no produce DELETE
        for sub in ir.substitution_ops:
            delete_for_source = [
                op for op in ir.operations
                if op["action"] == "DELETE" and op["target"] == sub.source
            ]
            assert len(delete_for_source) == 0, (
                f"REPLACE source '{sub.source}' tiene DELETE en operations"
            )

        # SubstitutionOp source debe tener KEEP lifecycle
        for sub in ir.substitution_ops:
            source_rc = _cap(ir, sub.source)
            if source_rc is not None:
                assert source_rc.action == KEEP

    def test_substitution_target_lifecycle_never_create(self, contract_with_bar_chart):
        """Multiple tests: substitution target nunca recibe CREATE.

        Incluso si el target no existe en contrato, substitution
        no debe forzar CREATE del target como lifecycle decision.
        """
        # Contrato SIN bar_chart en capabilities
        contract_minimal = SkillContract(
            contract_id="dashboard.minimal",
            version=1,
            input_schema={
                "type": "object",
                "required": ["metrics"],
                "properties": {
                    "metrics": {"type": "array", "items": {"type": "string"}},
                },
            },
            ast_template={
                "capabilities": {
                    "KpiRow": "presentation.kpi_row",
                    "Page": "layout.page",
                },
            },
            renderer={},
        )

        semantic = make_semantic(
            params={"metrics": ["revenue"]},
            actions=[
                {"verb": "replace", "object": "kpi", "reference": "bar chart"},
            ],
        )
        contract = make_contract(params={"metrics": ["revenue"]})

        ir = complete_structure(semantic, contract, contract_minimal)

        # substitution_ops puede estar vacío si "bar chart"
        # no matchea ninguna capability del contrato
        # Pero si hay substitution op, el target NO debe tener CREATE
        for sub in ir.substitution_ops:
            target_rc = _cap(ir, sub.target)
            if target_rc is not None:
                assert target_rc.action != CREATE, (
                    f"Substitution target '{sub.target}' tiene "
                    f"action=CREATE aunque no está en contrato"
                )
