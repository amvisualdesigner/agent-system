"""S2·F4 — El Confirmed Plan es la ÚNICA autoridad del WHAT.

Tras la confirmación, `action.target_capability` es la única fuente válida de
la capability. NO existe matching/inferencia post-confirmación, ni fallback,
ni compatibilidad legacy: si la acción confirmada no trae target_capability,
el resultado es CONFLICT (nunca una capability inferida).

Matriz (A–J) + invariantes de autoridad semántica.
Casos puramente unitarios sobre complete_structure(); los conflictos tipificados
de apply_engine viven en tests/e2e/test_f4_what_authority_conflicts.py.
"""

import pytest

from app.contracts.contract_resolution import ContractResolution
from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.skill_registry import SkillContract

CREATE = "CREATE"
MODIFY = "MODIFY"
DELETE = "DELETE"
KEEP = "KEEP"


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _contract(caps: dict[str, str], contract_id: str = "test.metrics") -> SkillContract:
    return SkillContract(
        contract_id=contract_id,
        version=1,
        input_schema={"type": "object", "required": [], "properties": {}},
        ast_template={"layout": None, "slots": [], "capabilities": dict(caps)},
        renderer={},
    )


def _semantic(actions: list[dict], params: dict | None = None) -> SemanticResolution:
    return SemanticResolution(
        semantic_params=params or {},
        semantic_provenance={k: "user_explicit" for k in (params or {})},
        confidence=1.0,
        actions=actions,
    )


def _contract_res(params: dict | None = None) -> ContractResolution:
    return ContractResolution(
        contract_params=params or {},
        contract_provenance={k: "contract" for k in (params or {})},
        confidence=1.0,
    )


def _ir(contract: SkillContract, actions: list[dict], params: dict | None = None):
    from app.engine.structural_completion import complete_structure

    return complete_structure(
        _semantic(actions, params), _contract_res(params), contract,
    )


def _actions(ir) -> dict[str, str]:
    return {rc.name: rc.action for rc in ir.capabilities}


def _complete_structure_source() -> str:
    import inspect

    from app.engine import structural_completion as sc

    return inspect.getsource(sc.complete_structure)


# Caps reales y distinguibles. 'metric'/'chart' son object keywords que el
# matching legacy sabía resolver (kpi_row / chart.bar) → buenos discriminantes.
METRIC_CONTRACT = _contract({
    "ChartBar": "presentation.chart.bar",
    "KpiRow": "presentation.kpi_row",
    "MetricCard": "presentation.metric_card",
    "Page": "layout.page",
})

PARAMS = {"metrics": ["revenue"], "metric": "revenue"}


# ═══════════════════════════════════════════════════════════════════
# A — exacto
# ═══════════════════════════════════════════════════════════════════


class TestCaseA_Exact:
    def test_create_confirmed_capability(self):
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "create", "object": "kpi",
              "target_capability": "presentation.kpi_row"}],
            PARAMS,
        )
        acts = _actions(ir)
        assert acts["presentation.kpi_row"] == CREATE
        assert acts["presentation.metric_card"] == KEEP

    def test_confirmed_capability_is_the_only_created_one(self):
        """Una sola acción create → exactamente una capability en CREATE."""
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "create", "object": "line",
              "target_capability": "presentation.metric_card"}],
            PARAMS,
        )
        creates = [n for n, a in _actions(ir).items() if a == CREATE]
        assert creates == ["presentation.metric_card"]


# ═══════════════════════════════════════════════════════════════════
# B — capabilities similares
# ═══════════════════════════════════════════════════════════════════


class TestCaseB_SimilarCapabilities:
    def test_explicit_metric_card_never_becomes_kpi_row(self):
        """Plan = metric_card con kpi_row presente → SIEMPRE metric_card.

        Antes de S2·F4 el matching legacy ('metric' → kpi_row) habría creado
        presentation.kpi_row.
        """
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "create", "object": "metric",
              "target_capability": "presentation.metric_card"}],
            PARAMS,
        )
        acts = _actions(ir)
        assert acts["presentation.metric_card"] == CREATE
        assert acts["presentation.kpi_row"] != CREATE

    def test_explicit_kpi_row_never_becomes_chart_bar(self):
        """object 'chart' (legacy → chart.bar) no puede desviar el target."""
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "create", "object": "chart",
              "target_capability": "presentation.kpi_row"}],
            PARAMS,
        )
        acts = _actions(ir)
        assert acts["presentation.kpi_row"] == CREATE
        assert acts["presentation.chart.bar"] != CREATE


# ═══════════════════════════════════════════════════════════════════
# C — mismo object
# ═══════════════════════════════════════════════════════════════════


class TestCaseC_SameObject:
    @pytest.mark.parametrize("cap", [
        "presentation.kpi_row", "presentation.metric_card",
        "presentation.chart.bar",
    ])
    def test_object_never_rewrites_target(self, cap):
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "modify", "object": "metric", "target_capability": cap}],
            PARAMS,
        )
        acts = _actions(ir)
        assert acts[cap] == MODIFY
        others = [n for n in acts if n != cap]
        assert all(acts[n] != MODIFY for n in others)

    def test_object_mismatch_does_not_rebind(self):
        """object que NO corresponde al target: manda el target."""
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "create", "object": "table",
              "target_capability": "presentation.chart.bar"}],
            PARAMS,
        )
        acts = _actions(ir)
        assert acts["presentation.chart.bar"] == CREATE
        assert "presentation.table" not in acts


# ═══════════════════════════════════════════════════════════════════
# D — capability inexistente (validación ≠ autoridad)
# ═══════════════════════════════════════════════════════════════════


class TestCaseD_UnknownCapability:
    def test_unknown_capability_never_materialized(self):
        """WHAT fuera del universo expresable NO entra en el IR."""
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "create", "object": "inexistente",
              "target_capability": "capability.inexistente"}],
            PARAMS,
        )
        acts = _actions(ir)
        assert "capability.inexistente" not in acts
        assert all(a == KEEP for a in acts.values())

    def test_unknown_capability_never_substitutes_similar(self):
        """No hay fallback: no materializa ninguna cap parecida."""
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "create", "object": "chart",
              "target_capability": "presentation.chart.pie"}],
            PARAMS,
        )
        acts = _actions(ir)
        assert "presentation.chart.pie" not in acts
        assert all(a == KEEP for a in acts.values())


# ═══════════════════════════════════════════════════════════════════
# E — lifecycle explícito
# ═══════════════════════════════════════════════════════════════════


class TestCaseE_Lifecycle:
    def test_lifecycle_comes_from_confirmed_verb(self):
        ir = _ir(METRIC_CONTRACT, [
            {"verb": "create", "object": "kpi",
             "target_capability": "presentation.kpi_row"},
            {"verb": "modify", "object": "metric",
             "target_capability": "presentation.metric_card"},
            {"verb": "remove", "object": "page",
             "target_capability": "layout.page"},
        ], PARAMS)
        acts = _actions(ir)
        assert acts["presentation.kpi_row"] == CREATE
        assert acts["presentation.metric_card"] == MODIFY
        assert acts["layout.page"] == DELETE

    def test_create_never_degrades_to_modify(self):
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "create", "object": "kpi",
              "target_capability": "presentation.kpi_row"}],
            PARAMS,
        )
        assert _actions(ir)["presentation.kpi_row"] == CREATE

    def test_delete_is_not_substituted(self):
        """DELETE explícito no se convierte en MODIFY por matching."""
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "remove", "object": "chart",
              "target_capability": "presentation.chart.bar"}],
            PARAMS,
        )
        assert _actions(ir)["presentation.chart.bar"] == DELETE


# ═══════════════════════════════════════════════════════════════════
# F — attach: WHAT y WHERE independientes
# ═══════════════════════════════════════════════════════════════════


class TestCaseF_AttachIndependent:
    ATTACH = {
        "target": {"capability": "layout.page",
                   "instance_label": "SalesOverviewPage"},
        "kind": "container",
        "provenance": "candidate",
    }

    def test_attach_binds_to_confirmed_capability(self):
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "create", "object": "metric",
              "target_capability": "presentation.metric_card",
              "attach": dict(self.ATTACH)}],
            PARAMS,
        )
        ops = {op["target"]: op for op in ir.operations}
        assert ops["presentation.metric_card"]["attach_to"] == self.ATTACH

    def test_attach_never_binds_to_another_capability(self):
        """Con caps que comparten object, el attach va solo a la confirmada."""
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "create", "object": "metric",
              "target_capability": "presentation.metric_card",
              "attach": dict(self.ATTACH)}],
            PARAMS,
        )
        others = [op for op in ir.operations
                  if op["target"] != "presentation.metric_card"]
        assert all("attach_to" not in op for op in others)

    def test_attach_without_capability_is_not_bound(self):
        """Sin target_capability no hay binding (nunca por inferencia)."""
        with pytest.raises(ValueError, match="target_capability"):
            _ir(
                METRIC_CONTRACT,
                [{"verb": "create", "object": "metric",
                  "attach": dict(self.ATTACH)}],
                PARAMS,
            )


# ═══════════════════════════════════════════════════════════════════
# Invariantes de autoridad semántica
# ═══════════════════════════════════════════════════════════════════


class TestAuthorityInvariants:
    def test_missing_target_capability_is_conflict_not_inference(self):
        """Acción confirmada sin target_capability → error duro (CONFLICT)."""
        with pytest.raises(ValueError) as exc:
            _ir(METRIC_CONTRACT, [{"verb": "create", "object": "kpi"}], PARAMS)
        assert "target_capability" in str(exc.value)
        assert "only WHAT authority" in str(exc.value)

    def test_blank_target_capability_is_conflict(self):
        with pytest.raises(ValueError, match="target_capability"):
            _ir(
                METRIC_CONTRACT,
                [{"verb": "create", "object": "kpi",
                  "target_capability": "   "}],
                PARAMS,
            )

    def test_inference_functions_removed(self):
        """No existe matching post-confirmación en el módulo."""
        import app.engine.structural_completion as sc

        assert not hasattr(sc, "_match_actions_to_capabilities")
        assert not hasattr(sc, "_match_single_capability")

    def test_frame_carries_target_capability(self):
        """El frame transporta el WHAT explícito (transporte S2·F4)."""
        from app.intent.models import ConfirmedIntent, IntentAction
        from app.intent.plan_compiler import compile_plan

        plan = compile_plan(ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[IntentAction(
                verb="create", target_capability="presentation.kpi_row",
                params={"metrics": ["revenue"]},
            )],
            params={"metrics": ["revenue"]},
            user_message="add kpi",
            interpretation_id="f4-transport",
        )).to_dict()
        frame_action = plan["semantic_frame"]["actions"][0]
        assert frame_action["target_capability"] == "presentation.kpi_row"
        assert plan["actions"][0]["target_capability"] == "presentation.kpi_row"

    def test_apply_engine_has_no_matcher_reference(self):
        """G1 valida por índice contra el plan, sin invocar matching."""
        import inspect

        from app.engine import apply_engine as ae

        src = inspect.getsource(ae._detect_dropped_actions)
        assert "_match_actions_to_capabilities" not in src
        assert "_match_single_capability" not in src
        assert "OBJECT_KEYWORDS" not in src
        assert "_OBJECT_KEYWORDS" not in src

    def test_postpass_b_uses_contract_caps_only(self):
        """El post-pass B trabaja sobre el contrato, no sobre caps inferidas."""
        import inspect

        import app.engine.structural_completion as sc

        assert "_infer_capabilities_from_contract(contract)" in inspect.getsource(
            sc.complete_structure
        )

    def test_no_post_confirmation_lifecycle_injection(self):
        """S2·F4: params NO inyectan lifecycle en caps no confirmadas.

        Antes de S2·F4 el post-pass A convertía cualquier signal de metrics en
        MODIFY presentation.kpi_row aunque el plan no la confirmara, y el
        post-pass B hacía lo mismo con todos los presentation.* cuando el
        object era 'dashboard'.
        """
        src = _complete_structure_source()

        # Post-pass A: señal de params → MODIFY kpi_row
        assert 'action_map["presentation.kpi_row"] = "modify"' not in src
        assert "metrics_signals" not in src
        # Post-pass B: modify dashboard → MODIFY presentation.*
        assert 'obj in ("dashboard",)' not in src
        assert '_VERBS_MODIFY and obj' not in src

    def test_unconfirmed_capability_is_never_given_an_action(self):
        """Una cap del contrato sin acción confirmada queda en KEEP."""
        for cap, params in (
            ("presentation.kpi_row", {"metrics": ["revenue"]}),
            ("presentation.table", {"columns": ["region"]}),
        ):
            ir = _ir(
                _contract({cap: cap}),
                [{"verb": "create", "object": "metric_card",
                  "target_capability": "presentation.metric_card"}],
                params,
            )
            acts = _actions(ir)
            if cap in acts:  # cap presente en contrato pero no confirmada
                assert acts[cap] != CREATE
                assert acts[cap] != MODIFY


# ═══════════════════════════════════════════════════════════════════
# H — MODIFY por params
# ═══════════════════════════════════════════════════════════════════


class TestCaseH_ParamsOnly:
    def test_modify_with_params_keeps_target(self):
        """MODIFY por params no selecciona otra capability."""
        ir = _ir(
            METRIC_CONTRACT,
            [{"verb": "modify", "object": "metric",
              "target_capability": "presentation.chart.bar"}],
            {"metrics": ["revenue"]},
        )
        acts = _actions(ir)
        assert acts["presentation.chart.bar"] == MODIFY
        assert acts["presentation.kpi_row"] == KEEP
        assert acts["presentation.metric_card"] == KEEP