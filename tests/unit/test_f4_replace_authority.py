"""S2·F4 — REPLACE también es proyección del Confirmed Plan.

Convención F3 (intents/interpreter.py):
    source_capability = capability SUSTITUIDA
    target_capability = capability NUEVA

Los dos extremos de SubstitutionOp salen de esos campos explícitos. Ni
`object`, ni `reference`, ni keywords, ni aliases, ni `_match_single_object`
pueden decidirlos después de la confirmación.
"""

import inspect

import pytest

from app.contracts.contract_resolution import ContractResolution
from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.skill_registry import SkillContract

CREATE = "CREATE"
KEEP = "KEEP"


def _contract(caps: dict[str, str]) -> SkillContract:
    return SkillContract(
        contract_id="test.replace",
        version=1,
        input_schema={"type": "object", "required": [], "properties": {}},
        ast_template={"layout": None, "slots": [], "capabilities": dict(caps)},
        renderer={},
    )


def _ir(contract: SkillContract, actions: list[dict], params: dict | None = None):
    from app.engine.structural_completion import complete_structure

    sem = SemanticResolution(
        semantic_params=params or {}, semantic_provenance={}, confidence=1.0,
        actions=actions,
    )
    cr = ContractResolution(
        contract_params=params or {}, contract_provenance={}, confidence=1.0,
    )
    return complete_structure(sem, cr, contract, {"actions": actions, "objects": [],
                                                 "constraints": [], "confidence": 1.0,
                                                 "missing_info": []})


def _ops(ir):
    return [(s.source, s.target) for s in ir.substitution_ops]


CONTRACT = _contract({
    "KpiRow": "presentation.kpi_row",
    "MetricCard": "presentation.metric_card",
    "ChartBar": "presentation.chart.bar",
    "Timeseries": "presentation.timeseries",
    "Page": "layout.page",
})
PARAMS = {"metrics": ["revenue"], "metric": "revenue"}


# ═══════════════════════════════════════════════════════════════════
# 1 — REPLACE exacto source → target
# ═══════════════════════════════════════════════════════════════════


class TestReplaceExact:
    def test_source_and_target_come_from_the_plan(self):
        action = {
            "verb": "replace",
            "source_capability": "presentation.kpi_row",
            "target_capability": "presentation.timeseries",
        }
        assert _ops(_ir(CONTRACT, [action], PARAMS)) == [
            ("presentation.kpi_row", "presentation.timeseries"),
        ]

    def test_mandatory_example_direction(self):
        """source=kpi_row, target=timeseries → NUNCA invertido."""
        action = {
            "verb": "replace",
            "object": "timeseries",
            "direct_object": "timeseries",
            "reference": "kpi",
            "source_capability": "presentation.kpi_row",
            "target_capability": "presentation.timeseries",
        }
        ops = _ops(_ir(CONTRACT, [action], PARAMS))
        assert ops == [("presentation.kpi_row", "presentation.timeseries")]
        assert ops != [("presentation.timeseries", "presentation.kpi_row")]


# ═══════════════════════════════════════════════════════════════════
# 2 — objects ambiguos
# ═══════════════════════════════════════════════════════════════════


class TestReplaceAmbiguousObjects:
    def test_ambiguous_object_text_cannot_change_identity(self):
        """object 'chart' (keyword) no puede decidir ninguno de los extremos."""
        action = {
            "verb": "replace",
            "object": "chart",
            "reference": "chart",
            "source_capability": "presentation.chart.bar",
            "target_capability": "presentation.metric_card",
        }
        assert _ops(_ir(CONTRACT, [action], PARAMS)) == [
            ("presentation.chart.bar", "presentation.metric_card"),
        ]

    def test_reference_text_never_becomes_target(self):
        action = {
            "verb": "replace",
            "object": "kpi",
            "reference": "chart bar",
            "source_capability": "presentation.kpi_row",
            "target_capability": "presentation.timeseries",
        }
        ops = _ops(_ir(CONTRACT, [action], PARAMS))
        assert ops == [("presentation.kpi_row", "presentation.timeseries")]
        assert "presentation.chart.bar" not in ops[0]


# ═══════════════════════════════════════════════════════════════════
# 3 — source y target con objects similares
# ═══════════════════════════════════════════════════════════════════


class TestReplaceSimilarObjects:
    def test_similar_objects_keep_plan_identity(self):
        """kpi_row y metric_card se parecen ('metric'); manda el Plan."""
        action = {
            "verb": "replace",
            "object": "metric",
            "reference": "metric",
            "source_capability": "presentation.metric_card",
            "target_capability": "presentation.kpi_row",
        }
        assert _ops(_ir(CONTRACT, [action], PARAMS)) == [
            ("presentation.metric_card", "presentation.kpi_row"),
        ]

    def test_same_capability_produces_no_op(self):
        action = {
            "verb": "replace",
            "source_capability": "presentation.kpi_row",
            "target_capability": "presentation.kpi_row",
        }
        assert _ops(_ir(CONTRACT, [action], PARAMS)) == []


# ═══════════════════════════════════════════════════════════════════
# 4 — el matching textual habría producido la inversión anterior
# ═══════════════════════════════════════════════════════════════════


class TestReplaceNoLegacyInversion:
    def test_legacy_object_reference_inversion_is_impossible(self):
        """Antes: op.source=match(object), op.target=match(reference).

        Con object='timeseries'/reference='kpi' eso daba
        (timeseries → kpi_row). Ahora los extremos son explícitos.
        """
        action = {
            "verb": "replace",
            "object": "timeseries",
            "reference": "kpi",
            "source_capability": "presentation.kpi_row",
            "target_capability": "presentation.timeseries",
        }
        ops = _ops(_ir(CONTRACT, [action], PARAMS))
        assert ops == [("presentation.kpi_row", "presentation.timeseries")]
        assert (ops[0][0], ops[0][1]) != (
            action["target_capability"], action["source_capability"],
        )

    def test_no_substitution_op_without_explicit_source(self):
        """Sin source_capability no hay op (no se reconstruye por texto)."""
        action = {
            "verb": "replace",
            "object": "kpi",
            "reference": "bar chart",
            "target_capability": "presentation.chart.bar",
        }
        assert _ops(_ir(CONTRACT, [action], PARAMS)) == []

    def test_substitution_source_is_never_matching_based(self):
        import app.engine.structural_completion as sc

        src = inspect.getsource(sc._extract_substitution_ops)
        assert "_match_single_object" not in src
        assert "_OBJECT_KEYWORDS" not in src
        assert 'action.get("target_capability")' in src
        assert 'action.get("source_capability")' in src
        # El argumento de contract_caps ya no decide nada.
        assert "_match" not in src.replace("_match_single_object", "")

    def test_layout_hints_replace_branch_is_explicit(self):
        import app.engine.structural_completion as sc

        src = inspect.getsource(sc._extract_layout_hints)
        replace_branch = src[src.index("_VERBS_REPLACE"):]
        assert "_match_single_object" not in replace_branch
        assert 'action.get("source_capability")' in replace_branch
        assert 'action.get("target_capability")' in replace_branch

    def test_match_single_object_only_survives_for_move_anchor(self):
        """`_match_single_object` queda únicamente para el anchor WHERE de MOVE."""
        import app.engine.structural_completion as sc

        callers = [
            name for name, obj in vars(sc).items()
            if inspect.isfunction(obj) and name != "_match_single_object"
            and "_match_single_object" in inspect.getsource(obj)
        ]
        assert callers == ["_extract_layout_hints"], callers


# ═══════════════════════════════════════════════════════════════════
# 9 — lifecycle REPLACE
# ═══════════════════════════════════════════════════════════════════


class TestReplaceLifecycle:
    def test_replace_produces_no_lifecycle_action(self):
        """REPLACE → KEEP: la sustitución es semántica, no lifecycle."""
        action = {
            "verb": "replace",
            "object": "kpi",
            "reference": "timeseries",
            "source_capability": "presentation.kpi_row",
            "target_capability": "presentation.timeseries",
        }
        ir = _ir(CONTRACT, [action], PARAMS)
        acts = {rc.name: rc.action for rc in ir.capabilities}
        assert acts["presentation.timeseries"] == KEEP
        assert acts["presentation.kpi_row"] == KEEP

    def test_substitution_target_not_promoted_to_lifecycle(self):
        """Backdoor A: el target de la sustitución no recibe lifecycle."""
        action = {
            "verb": "replace",
            "source_capability": "presentation.kpi_row",
            "target_capability": "presentation.chart.bar",
        }
        ir = _ir(CONTRACT, [action], PARAMS)
        acts = {rc.name: rc.action for rc in ir.capabilities}
        # Presente por el closed-world del contrato, pero sin acción.
        assert acts.get("presentation.chart.bar", KEEP) == KEEP
        assert all(a == KEEP for a in acts.values())

    def test_replace_does_not_touch_other_capabilities(self):
        action = {
            "verb": "replace",
            "source_capability": "presentation.kpi_row",
            "target_capability": "presentation.timeseries",
        }
        ir = _ir(CONTRACT, [action], PARAMS)
        assert all(rc.action == KEEP for rc in ir.capabilities)


# ═══════════════════════════════════════════════════════════════════
# 8 — attach + REPLACE
# ═══════════════════════════════════════════════════════════════════


class TestAttachWithReplace:
    ATTACH = {
        "target": {"capability": "layout.page", "instance_label": "SalesOverviewPage"},
        "kind": "container",
        "provenance": "candidate",
    }

    def test_attach_on_replace_does_not_bind(self):
        """attach es WHERE de CREATE; REPLACE no materializa componente."""
        action = {
            "verb": "replace",
            "source_capability": "presentation.kpi_row",
            "target_capability": "presentation.timeseries",
            "attach": dict(self.ATTACH),
        }
        ir = _ir(CONTRACT, [action], PARAMS)
        assert _ops(ir) == [("presentation.kpi_row", "presentation.timeseries")]
        assert all("attach_to" not in op for op in ir.operations)

    def test_attach_binds_to_confirmed_create_alongside_replace(self):
        """Con REPLACE + CREATE, el attach va solo a la capability confirmada."""
        actions = [
            {"verb": "replace",
             "source_capability": "presentation.kpi_row",
             "target_capability": "presentation.chart.bar"},
            {"verb": "create", "object": "metric",
             "target_capability": "presentation.metric_card",
             "attach": dict(self.ATTACH)},
        ]
        ir = _ir(CONTRACT, actions, PARAMS)
        assert _ops(ir) == [("presentation.kpi_row", "presentation.chart.bar")]
        ops = {op["target"]: op for op in ir.operations}
        assert ops["presentation.metric_card"]["action"] == CREATE
        assert ops["presentation.metric_card"]["attach_to"] == self.ATTACH


# ═══════════════════════════════════════════════════════════════════
# 10 — provenance de substitution FileOps
# ═══════════════════════════════════════════════════════════════════


class TestSubstitutionFileOpProvenance:
    def _ir_with_replace(self):
        action = {
            "verb": "replace",
            "source_capability": "presentation.kpi_row",
            "target_capability": "presentation.timeseries",
        }
        return _ir(CONTRACT, [action], PARAMS)

    def test_traceable_substitution_fileops_pass_provenance(self):
        from app.engine.apply_engine import validate_fileop_plan_provenance
        from app.graphir.utils import FileOp

        ir = self._ir_with_replace()
        fileops = [
            FileOp(action="create", path="frontend/src/components/charts/Trend.tsx",
                   content="x", pipeline_route="substitution",
                   metadata={"target_capability": "presentation.timeseries"}),
            FileOp(action="modify", path="frontend/src/pages/SalesOverviewPage.tsx",
                   content="x", pipeline_route="substitution",
                   metadata={"old_capability": "presentation.kpi_row",
                             "new_capability": "presentation.timeseries"}),
        ]
        assert validate_fileop_plan_provenance(fileops, ir) == []

    def test_untraceable_substitution_create_is_rejected(self):
        """Una capability materializable no venida del Plan se bloquea."""
        from app.engine.apply_engine import validate_fileop_plan_provenance
        from app.graphir.utils import FileOp

        ir = self._ir_with_replace()
        fileops = [FileOp(
            action="create", path="frontend/src/components/charts/Trend.tsx",
            content="x", pipeline_route="substitution",
            metadata={"target_capability": "presentation.metric_card"},
        )]
        violations = validate_fileop_plan_provenance(fileops, ir)
        assert len(violations) == 1
        assert "not traceable" in violations[0]

    def test_untraceable_substitution_modify_is_rejected(self):
        from app.engine.apply_engine import validate_fileop_plan_provenance
        from app.graphir.utils import FileOp

        ir = self._ir_with_replace()
        fileops = [FileOp(
            action="modify", path="frontend/src/pages/SalesOverviewPage.tsx",
            content="x", pipeline_route="substitution",
            metadata={"old_capability": "presentation.timeseries",
                      "new_capability": "presentation.kpi_row"},
        )]
        violations = validate_fileop_plan_provenance(fileops, ir)
        assert len(violations) == 1
        assert "not traceable" in violations[0]

    def test_substitution_without_metadata_is_rejected(self):
        from app.engine.apply_engine import validate_fileop_plan_provenance
        from app.graphir.utils import FileOp

        ir = self._ir_with_replace()
        fileops = [FileOp(action="create", path="frontend/src/x.tsx", content="x",
                          pipeline_route="substitution")]
        violations = validate_fileop_plan_provenance(fileops, ir)
        assert len(violations) == 1
        assert "not traceable" in violations[0]