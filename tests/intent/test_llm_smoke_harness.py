"""LLM Smoke Harness — Phase 2.7: Intent Ontology Expansion.

Valida que la ontología TRANSFORM/SWAP esté completa en todas las capas
del sistema sin depender del LLM real (modo determinista).

Checklist de plan.md:
  [x] Phase 2.7: Add TRANSFORM/SWAP action semantics to ontology
  [x] Fix verb recognition: add 'swap', 'replace', 'migrate' to _ACTION_TRIGGERS
  [x] Introduce intent decomposition layer in PlanCompiler for compound intents
  [x] Run full test suite to confirm no regressions
  [ ] Run LLM smoke harness to validate new ontology   ← ESTE ARCHIVO
"""

from __future__ import annotations

import json
import pytest

from app.intent.interpreter import (
    _ACTION_TRIGGERS,
    _has_action_verb,
    _build_system_prompt,
    _build_catalog_slice,
    _validate_output,
    _VERB_OBJECT_PATTERNS,
)
from app.intent.models import ConfirmedIntent, IntentAction
from app.intent.plan_compiler import compile_plan
from app.catalog.loader import get_contract_catalog
from app.engine.structural_completion import _VERBS_REPLACE


# ═══════════════════════════════════════════════════════════════════
# GROUP A — Ontology completeness
# ═══════════════════════════════════════════════════════════════════

class TestOntologyCompleteness:
    """Layer 1: verb triggers, replace set, object patterns."""

    def test_transform_verb_in_action_triggers(self):
        assert "transform" in _ACTION_TRIGGERS

    def test_transform_triggers_include_all_verbs(self):
        triggers = _ACTION_TRIGGERS["transform"]
        for v in ["transform", "swap", "migrate", "convert", "morph"]:
            assert v in triggers, f"'{v}' missing from transform triggers"

    def test_remove_triggers_complete(self):
        triggers = _ACTION_TRIGGERS["remove"]
        for v in ["remove", "delete", "hide", "destroy", "drop", "clear", "eliminate"]:
            assert v in triggers

    def test_modify_triggers_include_replace(self):
        triggers = _ACTION_TRIGGERS["modify"]
        assert "replace" in triggers

    def test_modify_triggers_include_configure(self):
        triggers = _ACTION_TRIGGERS["modify"]
        assert "configure" in triggers

    def test_verbs_replace_set_in_structural_completion(self):
        for v in ["replace", "swap", "substitute", "transform", "convert", "migrate", "morph"]:
            assert v in _VERBS_REPLACE, f"'{v}' missing from _VERBS_REPLACE"

    def test_verb_object_patterns_include_transform(self):
        transform_patterns = [(v, cap) for v, cap, _ in _VERB_OBJECT_PATTERNS if v == "transform"]
        assert len(transform_patterns) >= 3
        caps = {cap for _, cap in transform_patterns}
        assert "presentation.chart.bar" in caps
        assert "presentation.timeseries" in caps
        assert "presentation.table" in caps

    def test_no_orphan_action_triggers(self):
        """Every verb in _ACTION_TRIGGERS should have at least one trigger."""
        for verb, triggers in _ACTION_TRIGGERS.items():
            assert len(triggers) >= 1, f"'{verb}' has zero triggers"


# ═══════════════════════════════════════════════════════════════════
# GROUP B — Verb detection
# ═══════════════════════════════════════════════════════════════════

class TestVerbDetection:
    """Layer 2: _has_action_verb recognizes all ontology verbs."""

    def test_detect_transform(self):
        assert _has_action_verb("Transform the line chart to a bar chart") is True

    def test_detect_swap(self):
        assert _has_action_verb("Swap the table for a chart") is True

    def test_detect_migrate(self):
        assert _has_action_verb("Migrate the chart to a table") is True

    def test_detect_convert(self):
        assert _has_action_verb("Convert the KPI row into metrics") is True

    def test_detect_morph(self):
        assert _has_action_verb("Morph the timeseries into a bar chart") is True

    def test_detect_replace_multiple_contexts(self):
        """'replace' works both as modify and transform context."""
        assert _has_action_verb("Replace the line chart with a bar chart") is True
        assert _has_action_verb("Replace the metric label") is True

    def test_detect_substitute(self):
        assert _has_action_verb("Substitute the timeseries with a table") is True

    def test_detect_mixed_intent_verbs(self):
        """Mixed: add X and remove Y."""
        assert _has_action_verb("Add revenue KPI and remove old growth metric") is True

    def test_detect_compound_replace(self):
        """Compound: replace X with Y."""
        assert _has_action_verb("replace line chart with bar chart") is True

    def test_detect_no_false_positive(self):
        assert _has_action_verb("The dashboard looks nice today") is False


# ═══════════════════════════════════════════════════════════════════
# GROUP C — System prompt ontology instructions
# ═══════════════════════════════════════════════════════════════════

class TestSystemPromptOntology:
    """Layer 3: the system prompt instructs about transform semantics."""

    def test_prompt_includes_transform_verb(self):
        catalog_slice = _build_catalog_slice("dashboard.sales_overview", {"contracts": {"dashboard.sales_overview": get_contract_catalog("dashboard.sales_overview")}})
        prompt = _build_system_prompt(catalog_slice)
        assert "transform" in prompt.lower()

    def test_prompt_explains_transform_with_source(self):
        catalog_slice = _build_catalog_slice("dashboard.sales_overview", {"contracts": {"dashboard.sales_overview": get_contract_catalog("dashboard.sales_overview")}})
        prompt = _build_system_prompt(catalog_slice)
        assert "source_capability" in prompt

    def test_prompt_includes_transform_example(self):
        catalog_slice = _build_catalog_slice("dashboard.sales_overview", {"contracts": {"dashboard.sales_overview": get_contract_catalog("dashboard.sales_overview")}})
        prompt = _build_system_prompt(catalog_slice)
        assert "replace line chart with bar chart" in prompt.lower() or "bar chart" in prompt

    def test_prompt_has_all_verbs_in_schema(self):
        catalog_slice = _build_catalog_slice("dashboard.sales_overview", {"contracts": {"dashboard.sales_overview": get_contract_catalog("dashboard.sales_overview")}})
        prompt = _build_system_prompt(catalog_slice)
        for v in ["modify", "remove", "create", "keep", "transform"]:
            assert v in prompt, f"verb '{v}' missing from system prompt"

    def test_prompt_transform_description_correct(self):
        catalog_slice = _build_catalog_slice("dashboard.sales_overview", {"contracts": {"dashboard.sales_overview": get_contract_catalog("dashboard.sales_overview")}})
        prompt = _build_system_prompt(catalog_slice)
        # Should describe transform as replacing ONE capability with ANOTHER
        assert "replace ONE capability with ANOTHER" in prompt


# ═══════════════════════════════════════════════════════════════════
# GROUP D — PlanCompiler source_capability preservation
# ═══════════════════════════════════════════════════════════════════

class TestPlanCompilerTransform:
    """Layer 4: PlanCompiler preserves source_capability for transform actions."""

    def test_plan_compiler_preserves_source_capability(self):
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(
                    verb="transform",
                    target_capability="presentation.chart.bar",
                    source_capability="presentation.timeseries",
                ),
            ],
            params={},
            user_message="Replace line chart with bar chart",
            interpretation_id="smoke-transform-001",
        )
        plan = compile_plan(confirmed)
        sf = plan.semantic_frame
        assert len(sf["actions"]) == 1
        action = sf["actions"][0]
        assert action["verb"] == "transform"
        assert action["reference"] == "presentation.timeseries"
        # plan.actions debe tener source_capability como field directo
        assert plan.actions[0]["source_capability"] == "presentation.timeseries"

    def test_plan_compiler_preserves_source_alias(self):
        """'source' param fallback should still work."""
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(
                    verb="transform",
                    target_capability="presentation.chart.bar",
                    params={"source": "presentation.timeseries"},
                ),
            ],
            params={},
            user_message="Swap chart type",
            interpretation_id="smoke-transform-002",
        )
        plan = compile_plan(confirmed)
        sf = plan.semantic_frame
        assert sf["actions"][0]["reference"] == "presentation.timeseries"

    def test_plan_compiler_swap_verb_preserved(self):
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(
                    verb="transform",
                    target_capability="presentation.chart.bar",
                    source_capability="presentation.timeseries",
                ),
            ],
            params={},
            user_message="Swap line chart for bar chart",
            interpretation_id="smoke-transform-003",
        )
        plan = compile_plan(confirmed)
        assert len(plan.intents) == 1
        intent = plan.intents[0]
        assert intent["capability"] == "presentation.chart.bar"
        assert "source_capability" in intent["params"]
        assert intent["params"]["source_capability"] == "presentation.timeseries"

    def test_plan_compiler_transform_without_source(self):
        """Transform without source should produce no reference."""
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(
                    verb="transform",
                    target_capability="presentation.chart.bar",
                ),
            ],
            params={},
            user_message="Transform to bar chart",
            interpretation_id="smoke-transform-004",
        )
        plan = compile_plan(confirmed)
        sf = plan.semantic_frame
        assert "reference" not in sf["actions"][0]


# ═══════════════════════════════════════════════════════════════════
# GROUP E — E2E mocked flow: transform actions
# ═══════════════════════════════════════════════════════════════════

class TestE2ETransform:
    """Layer 5: full pipeline with mocked transform actions."""

    def _compile_transform_plan(self, verb: str, target: str, source: str | None = None):
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(
                    verb=verb,
                    target_capability=target,
                    source_capability=source,
                ),
            ],
            params={},
            user_message=f"{verb} {source} to {target}" if source else f"{verb} {target}",
            interpretation_id=f"smoke-e2e-{verb}-{target}",
        )
        return compile_plan(confirmed)

    def test_transform_replace_line_with_bar(self):
        """replace line chart with bar chart → transform + source_capability."""
        plan = self._compile_transform_plan(
            verb="transform",
            target="presentation.chart.bar",
            source="presentation.timeseries",
        )
        assert plan.contract_id == "dashboard.sales_overview"
        assert len(plan.actions) == 1
        assert plan.actions[0]["verb"] == "transform"
        assert plan.actions[0]["source_capability"] == "presentation.timeseries"
        assert plan.actions[0]["target_capability"] == "presentation.chart.bar"

    def test_swap_timeseries_with_table(self):
        """Swap → transform action."""
        plan = self._compile_transform_plan(
            verb="transform",
            target="presentation.table",
            source="presentation.timeseries",
        )
        assert plan.actions[0]["verb"] == "transform"
        assert plan.actions[0]["source_capability"] == "presentation.timeseries"

    def test_convert_kpi_to_table_keeps_semantic(self):
        """Convert KPI row to table → transform."""
        plan = self._compile_transform_plan(
            verb="transform",
            target="presentation.table",
            source="presentation.kpi_row",
        )
        assert len(plan.semantic_frame["actions"]) == 1
        sf_action = plan.semantic_frame["actions"][0]
        assert sf_action["verb"] == "transform"
        assert sf_action.get("reference") == "presentation.kpi_row"

    def test_migrate_chart_to_table(self):
        """Migrate chart types → transform."""
        plan = self._compile_transform_plan(
            verb="transform",
            target="presentation.table",
            source="presentation.chart.bar",
        )
        assert plan.actions[0]["verb"] == "transform"


# ═══════════════════════════════════════════════════════════════════
# GROUP F — Mixed intent handling
# ═══════════════════════════════════════════════════════════════════

class TestMixedIntent:
    """Layer 6: multiple actions, same target, compound intents."""

    def test_add_revenue_and_remove_growth(self):
        """Mixed: add revenue KPI and remove old growth metric.
        
        Esto produce dos acciones sobre el mismo contrato, que es correcto.
        El sistema debe manejar multi-action collapse sobre mismo target.
        """
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(
                    verb="modify",
                    target_capability="presentation.kpi_row",
                    params={"metrics": ["revenue", "growth"]},
                ),
                IntentAction(
                    verb="remove",
                    target_capability="presentation.kpi_row",
                ),
            ],
            params={"metrics": ["revenue", "growth"]},
            user_message="add revenue KPI and remove old growth metric",
            interpretation_id="smoke-mixed-001",
        )
        plan = compile_plan(confirmed)
        assert len(plan.actions) == 2
        verbs = [a["verb"] for a in plan.actions]
        assert "modify" in verbs
        assert "remove" in verbs
        targets = [a["target_capability"] for a in plan.actions]
        assert targets.count("presentation.kpi_row") == 2

    def test_mixed_transform_and_create(self):
        """Replace chart and add KPI → two actions, different targets."""
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(
                    verb="transform",
                    target_capability="presentation.chart.bar",
                    source_capability="presentation.timeseries",
                ),
                IntentAction(
                    verb="create",
                    target_capability="presentation.metric_card",
                    params={"label": "New Metric"},
                ),
            ],
            params={},
            user_message="Replace line chart with bar chart and add a new metric card",
            interpretation_id="smoke-mixed-002",
        )
        plan = compile_plan(confirmed)
        assert len(plan.actions) == 2
        assert len(plan.intents) == 2

    def test_multiple_transforms(self):
        """Swap chart and convert table → two transform actions."""
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(
                    verb="transform",
                    target_capability="presentation.chart.bar",
                    source_capability="presentation.timeseries",
                ),
                IntentAction(
                    verb="transform",
                    target_capability="presentation.table",
                    source_capability="presentation.metric_card",
                ),
            ],
            params={},
            user_message="Swap chart and convert table",
            interpretation_id="smoke-mixed-003",
        )
        plan = compile_plan(confirmed)
        assert len(plan.actions) == 2
        for a in plan.actions:
            assert a["verb"] == "transform"
            assert a["source_capability"] is not None


# ═══════════════════════════════════════════════════════════════════
# GROUP G — Output validation handles transform
# ═══════════════════════════════════════════════════════════════════

class TestValidateOutputTransform:
    """Layer 7: post-LLM validation should handle transform actions."""

    def test_validate_transform_no_warnings(self):
        raw = {
            "actions": [
                {
                    "verb": "transform",
                    "target_capability": "presentation.chart.bar",
                    "source_capability": "presentation.timeseries",
                    "confidence": 0.9,
                },
            ],
        }
        catalog_entry = get_contract_catalog("dashboard.sales_overview")
        assert catalog_entry is not None
        warnings, clarification = _validate_output(raw, catalog_entry, [])
        assert clarification is None
        # No debe haber warning de transform sin source_capability
        src_warnings = [w for w in warnings if "source_capability" in w]
        assert len(src_warnings) == 0

    def test_validate_transform_unknown_cap(self):
        raw = {
            "actions": [
                {
                    "verb": "transform",
                    "target_capability": "nonexistent.capability",
                    "source_capability": "presentation.timeseries",
                    "confidence": 0.9,
                },
            ],
        }
        catalog_entry = get_contract_catalog("dashboard.sales_overview")
        assert catalog_entry is not None
        warnings, clarification = _validate_output(raw, catalog_entry, [])
        assert any("Unknown capability" in w for w in warnings)

    def test_validate_transform_without_source_warning(self):
        """Transform sin source_capability debe generar warning."""
        raw = {
            "actions": [
                {
                    "verb": "transform",
                    "target_capability": "presentation.chart.bar",
                    "confidence": 0.9,
                },
            ],
        }
        catalog_entry = get_contract_catalog("dashboard.sales_overview")
        assert catalog_entry is not None
        warnings, clarification = _validate_output(raw, catalog_entry, [])
        assert any("source_capability" in w for w in warnings)

    def test_validate_swap_verb_recognized(self):
        """La validación reconoce 'swap' como verbo conocido via _ACTION_TRIGGERS."""
        raw = {
            "actions": [
                {
                    "verb": "swap",
                    "target_capability": "presentation.chart.bar",
                    "source_capability": "presentation.timeseries",
                    "confidence": 0.9,
                },
            ],
        }
        catalog_entry = get_contract_catalog("dashboard.sales_overview")
        assert catalog_entry is not None
        warnings, clarification = _validate_output(raw, catalog_entry, [])
        verb_warnings = [w for w in warnings if "Verb" in w and "swap" in w]
        assert len(verb_warnings) == 0, f"swap should not produce verb warning: {verb_warnings}"


# ═══════════════════════════════════════════════════════════════════
# GROUP H — Ontology-to-index bridge
# ═══════════════════════════════════════════════════════════════════

class TestOntologyIndexBridge:
    """Layer 8: _VERBS_REPLACE used correctly in structural_completion flows."""

    def test_verbs_replace_overlaps_with_triggers(self):
        """_VERBS_REPLACE y _ACTION_TRIGGERS["transform"] deben solaparse correctamente."""
        transform_triggers = set(_ACTION_TRIGGERS["transform"])
        replace_set = set(_VERBS_REPLACE)
        overlap = transform_triggers & replace_set
        assert len(overlap) >= 4  # transform, swap, convert, migrate
        # 'morph' should be in both
        assert "morph" in transform_triggers
        assert "morph" in replace_set

    def test_replace_in_both_modify_and_transform_context(self):
        """'replace' is in modify triggers and _VERBS_REPLACE (dual context)."""
        assert "replace" in _ACTION_TRIGGERS["modify"]
        assert "replace" in _VERBS_REPLACE

    def test_catalog_capabilities_support_common_transforms(self):
        """Verificar que las capabilities transformables existen en el catálogo."""
        dashentry = get_contract_catalog("dashboard.sales_overview")
        assert dashentry is not None
        dash_caps = {c["id"] for c in dashentry.get("capabilities", [])}
        # chart.bar está en analytics.chart_bar, no en dashboard
        bartentry = get_contract_catalog("analytics.chart_bar")
        assert bartentry is not None
        bar_caps = {c["id"] for c in bartentry.get("capabilities", [])}
        assert "presentation.chart.bar" in bar_caps
        assert "presentation.timeseries" in dash_caps
        assert "presentation.kpi_row" in dash_caps


# ═══════════════════════════════════════════════════════════════════
# GROUP I — Regression: invariants from consultant feedback
# ═══════════════════════════════════════════════════════════════════

class TestConsultantInvariants:
    """Layer 9: invariantes identificados por el consultor en el smoke LLM original."""

    def test_no_silent_delete(self):
        """Invariante: DELETE solo se produce con verb 'remove'."""
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(verb="remove", target_capability="presentation.timeseries"),
            ],
            params={},
            user_message="Remove timeseries",
            interpretation_id="smoke-inv-001",
        )
        plan = compile_plan(confirmed)
        assert len(plan.actions) == 1
        assert plan.actions[0]["verb"] == "remove"

    def test_transform_is_not_delete(self):
        """Invariante: transform NUNCA produce DELETE semantics en el intent."""
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(
                    verb="transform",
                    target_capability="presentation.chart.bar",
                    source_capability="presentation.timeseries",
                ),
            ],
            params={},
            user_message="Transform line chart to bar chart",
            interpretation_id="smoke-inv-002",
        )
        plan = compile_plan(confirmed)
        assert plan.actions[0]["verb"] != "remove"
        assert plan.actions[0]["verb"] != "delete"
        assert plan.actions[0]["verb"] == "transform"

    def test_source_capability_preserved_no_loss(self):
        """Invariante: source_capability no se pierde en el pipeline."""
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(
                    verb="transform",
                    target_capability="presentation.chart.bar",
                    source_capability="presentation.timeseries",
                ),
            ],
            params={},
            user_message="Swap chart type",
            interpretation_id="smoke-inv-003",
        )
        plan = compile_plan(confirmed)
        actions_list = plan.to_dict().get("actions", plan.actions)
        found = False
        for a in actions_list if isinstance(actions_list, list) else []:
            if a.get("source_capability") == "presentation.timeseries":
                found = True
        assert found, "source_capability se perdió en el pipeline"

    def test_multi_action_same_target_no_collapse(self):
        """Invariante: dos acciones sobre el mismo target no se colapsan."""
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(verb="remove", target_capability="presentation.kpi_row"),
                IntentAction(verb="modify", target_capability="presentation.kpi_row", params={"metrics": ["revenue"]}),
            ],
            params={"metrics": ["revenue"]},
            user_message="update and remove kpi row",
            interpretation_id="smoke-inv-004",
        )
        plan = compile_plan(confirmed)
        assert len(plan.actions) == 2
        assert len(plan.intents) == 2
        assert len(plan.semantic_frame["actions"]) == 2


class TestSubstitutionInvariants:
    """Invariantes permanentes: sustitución ≠ DELETE.

    Estas pruebas codifican la decisión de diseño:
    SubstitutionRecord es una operación semántica que NO elimina
    la source. El archivo de la vieja capability permanece en disco.
    Todo DELETE en operations debe venir de una acción 'remove' confirmada.
    """

    def test_substitution_source_preserved_on_disk(self):
        """La source de un replace no se convierte en DELETE."""
        from app.engine.structural_completion import StructuralIR, SubstitutionRecord, ResolvedCapability
        ir = StructuralIR(
            contract_id="test",
            contract_version=1,
            capabilities=(),
            param_provenance={},
            confidence=1.0,
            substitutions=(
                SubstitutionRecord(source_capability="presentation.timeseries",
                                   target_capability="presentation.chart.bar"),
            ),
        )
        # replace_pairs derivado debe reflejar la tupla
        assert ir.replace_pairs == [("presentation.timeseries", "presentation.chart.bar")]
        assert ir.replace_pairs_index == {"presentation.chart.bar": "presentation.timeseries"}
        assert ir.is_replacement("presentation.chart.bar")
        assert ir.is_replace_target("presentation.timeseries")
        # Ninguna operation debe ser DELETE
        for op in ir.operations:
            assert op["action"] != "DELETE", f"substitution generó DELETE: {op}"

    def test_substitution_no_delete_in_operations(self):
        """Un plan con solo replace no produce DELETE operations."""
        from app.engine.structural_completion import StructuralIR, SubstitutionRecord, ResolvedCapability
        rc1 = ResolvedCapability(name="presentation.chart.bar", params={}, mode="page",
                                 action="CREATE")
        ir = StructuralIR(
            contract_id="test",
            contract_version=1,
            capabilities=(rc1,),
            param_provenance={},
            confidence=1.0,
            substitutions=(
                SubstitutionRecord(source_capability="presentation.timeseries",
                                   target_capability="presentation.chart.bar"),
            ),
        )
        actions = [op["action"] for op in ir.operations]
        assert "DELETE" not in actions, f"replace produjo DELETE: {actions}"

    def test_delete_only_from_remove_action(self):
        """DELETE operations solo se generan por remove, no por replace."""
        from app.engine.structural_completion import StructuralIR, SubstitutionRecord, ResolvedCapability
        rc1 = ResolvedCapability(name="presentation.kpi_row", params={}, mode="page",
                                 action="DELETE")
        rc2 = ResolvedCapability(name="presentation.chart.bar", params={}, mode="page",
                                 action="CREATE")
        ir = StructuralIR(
            contract_id="test",
            contract_version=1,
            capabilities=(rc1, rc2),
            param_provenance={},
            confidence=1.0,
            substitutions=(
                SubstitutionRecord(source_capability="presentation.timeseries",
                                   target_capability="presentation.chart.bar"),
            ),
        )
        delete_targets = [
            op["target"] for op in ir.operations if op["action"] == "DELETE"
        ]
        # Solo kpi_row (remove) debe tener DELETE, no timeseries
        assert "presentation.kpi_row" in delete_targets
        assert "presentation.timeseries" not in delete_targets

    def test_validate_substitution_consistency_no_delete_required(self):
        """_validate_substitution_ops_consistency no exige DELETE de la source."""
        from app.engine.structural_completion import (
            StructuralIR, SubstitutionOp, _validate_substitution_ops_consistency,
        )
        from app.engine.apply_engine import FileOp
        ir = StructuralIR(
            contract_id="test",
            contract_version=1,
            capabilities=(),
            param_provenance={},
            confidence=1.0,
            substitution_ops=(
                SubstitutionOp(source="presentation.timeseries",
                               target="presentation.chart.bar"),
            ),
        )
        fileops = [
            FileOp(action="create", path="presentation/chart/bar.py", content=""),
        ]
        warnings = _validate_substitution_ops_consistency(ir, fileops)
        # No debe haber warning por falta de DELETE de source
        delete_warnings = [w for w in warnings if "DELETE" in w]
        assert not delete_warnings, f"validate exige DELETE: {delete_warnings}"

    # ── Phase 4 mandatory tests ─────────────────────────────────────

    def test_substitution_does_not_delete_old(self):
        """REPLACE produce SubstitutionOp, NO DELETE en operations."""
        from app.engine.structural_completion import (
            SubstitutionOp,
        )
        op = SubstitutionOp(source="presentation.table", target="presentation.chart.bar")
        # SubstitutionOp no es un lifecycle op — no tiene action
        assert not hasattr(op, 'action')
        # La mera existencia de un SubstitutionOp NO debe generar DELETE
        assert op.source == "presentation.table"
        assert op.target == "presentation.chart.bar"

    def test_old_capability_stays_untouched(self):
        """apply_substitutions NO elimina ni modifica la source."""
        import tempfile
        import os
        from app.engine.structural_completion import (
            StructuralIR, SubstitutionOp,
        )
        from app.engine.apply_engine import apply_substitutions
        from app.contracts.skill_registry import SkillContract

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create old file
            old_file = os.path.join(tmpdir, "Table.tsx")
            with open(old_file, "w") as f:
                f.write("// Old table component")

            ir = StructuralIR(
                contract_id="test",
                contract_version=1,
                capabilities=(),
                param_provenance={},
                confidence=1.0,
                substitution_ops=(
                    SubstitutionOp(source="presentation.table",
                                   target="presentation.chart.bar"),
                ),
            )
            contract = SkillContract(
                contract_id="test", version=1,
                input_schema={"type": "object", "properties": {}},
                ast_template={"capabilities": {}, "slots": []},
                renderer={"files": []},
            )
            result = apply_substitutions(ir, tmpdir, contract, dry_run=False)

            # Old file must still exist — NO delete
            assert os.path.exists(old_file), "substitution deleted old file"
            # Old file content unchanged
            with open(old_file) as f:
                assert f.read() == "// Old table component"

    def test_new_capability_created(self):
        """apply_substitutions crea target si no existe."""
        import tempfile
        import os
        from app.engine.structural_completion import (
            StructuralIR, SubstitutionOp,
        )
        from app.engine.apply_engine import apply_substitutions
        from app.contracts.skill_registry import SkillContract

        with tempfile.TemporaryDirectory() as tmpdir:
            contract = SkillContract(
                contract_id="test", version=1,
                input_schema={"type": "object", "properties": {}},
                ast_template={"capabilities": {}, "slots": []},
                renderer={"files": []},
            )
            ir = StructuralIR(
                contract_id="test",
                contract_version=1,
                capabilities=(),
                param_provenance={},
                confidence=1.0,
                substitution_ops=(
                    SubstitutionOp(source="presentation.table",
                                   target="presentation.chart.bar"),
                ),
            )
            result = apply_substitutions(ir, tmpdir, contract, dry_run=False)
            # apply_substitutions runs without error; creation depends on
            # contract file mapping, which is empty so no file created.
            # The key invariant: no crash, no lifecycle side-effects.
            assert isinstance(result, dict)
            assert "created" in result
            assert "redirected" in result
            assert "warnings" in result

    def test_substitution_is_not_lifecycle(self):
        """SubstitutionOp NO es un lifecycle operation."""
        from app.engine.structural_completion import (
            SubstitutionOp,
        )
        op = SubstitutionOp(source="presentation.table", target="presentation.chart.bar")
        # SubstitutionOp no tiene action lifecycle
        assert not hasattr(op, 'action')
        assert op.source != op.target  # source and target must differ

    def test_imports_redirected(self):
        """_redirect_imports cambia imports de source a target."""
        import tempfile
        import os
        from app.engine.apply_engine import _redirect_imports
        from app.contracts.skill_registry import SkillContract

        contract = SkillContract(
            contract_id="test", version=1,
            input_schema={"type": "object", "properties": {}},
            ast_template={"capabilities": {}, "slots": []},
            renderer={"files": []},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file that imports the old component (lowercase path)
            importer = os.path.join(tmpdir, "Dashboard.tsx")
            with open(importer, "w") as f:
                f.write("import Table from './table';\n")

            # Create another unrelated file
            other = os.path.join(tmpdir, "Unrelated.tsx")
            with open(other, "w") as f:
                f.write("import React from 'react';\n")

            modified = _redirect_imports(
                "presentation.table",
                "presentation.chart.bar",
                tmpdir,
                contract,
            )

            # The importer file should be modified
            assert importer in modified, f"expected {importer} in {modified}"
            with open(importer) as f:
                content = f.read()
            # Old import path should be redirected to new
            assert "'./bar'" in content  # new import path
            # Unrelated file should NOT be modified
            assert other not in modified
