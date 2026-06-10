"""Phase 2.5: Semantic E2E tests — delete authority, ambiguity, replace safety.

Tests stop at the right layer:
  A, C → CompiledPlan level (no apply_engine)
  B     → complete_structure / resolver level
  D     → import/unit level (module existence, exception type)
  E     → _validate_delete_authority unit tests
"""

from __future__ import annotations

import dataclasses
import importlib
import os

import pytest

from app.engine.apply_engine import _validate_delete_authority
from app.engine.structural_completion import (
    StructuralIR, ResolvedCapability, CompletionMode,
)
from app.engine.errors import AmbiguousStructuralTargetError
from app.engine.structural_index import StructuralIndex
from app.graphir.structure.resolver import resolve
from app.graphir.structure.registry import StructuralRegistry
from tests.e2e.helpers import build_plan_from_actions, run_agent


# ── ActionView DTO — single representation for plan action inspection ──

@dataclasses.dataclass
class ActionView:
    verb: str
    target_capability: str
    instance_hint: str | None = None
    params: dict = dataclasses.field(default_factory=dict)

    @classmethod
    def from_action_dict(cls, d: dict) -> ActionView:
        return cls(
            verb=d.get("verb", ""),
            target_capability=d.get("target_capability", ""),
            instance_hint=d.get("instance_hint"),
            params=d.get("params", {}),
        )


def plan_actions(plan_or_confirm: dict | object) -> list[ActionView]:
    """Extract actions from CompiledPlan or confirm dict into ActionView list."""
    if isinstance(plan_or_confirm, dict):
        p = plan_or_confirm.get("plan") or plan_or_confirm
        raw = p.get("actions", []) if isinstance(p, dict) else []
    elif hasattr(plan_or_confirm, "actions"):
        raw = list(plan_or_confirm.actions)
    else:
        raw = []
    return [ActionView.from_action_dict(a) if isinstance(a, dict) else ActionView(verb=getattr(a, "verb", ""), target_capability=getattr(a, "target_capability", "")) for a in raw]


# ── StructuralIR helper ──

def _make_structural_ir(actions: list[dict]) -> StructuralIR:
    caps = []
    for a in actions:
        caps.append(ResolvedCapability(
            name=a["target"],
            params={},
            mode=CompletionMode.SAFE_COMPLETE,
            action=a["action"],
            provenance={"source": "test"},
        ))
    return StructuralIR(
        contract_id="test",
        contract_version=1,
        capabilities=tuple(caps),
        param_provenance={},
        confidence=1.0,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A + C — Plan-level action assertions (CompiledPlan, no apply_engine)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlanActions:
    """Verificar que plan.actions refleja la intencion del usuario literalmente.
    Sin interpretacion: CREATE no produce remove, remove produce remove."""

    def test_create_intent_no_remove(self):
        confirm = run_agent("add a line chart")
        assert confirm["status"] == "ok"
        verbs = {a.verb for a in plan_actions(confirm)}
        assert "remove" not in verbs, f"CREATE-only intent should not contain 'remove': {verbs}"

    def test_compound_intent_no_remove(self):
        plan = build_plan_from_actions([
            {"verb": "create", "target_capability": "presentation.timeseries"},
            {"verb": "modify", "target_capability": "layout.page"},
        ])
        verbs = {a.verb for a in plan_actions(plan)}
        assert "remove" not in verbs

    def test_remove_intent_has_remove(self):
        confirm = run_agent("remove the KPI row")
        assert confirm["status"] == "ok"
        verbs = {a.verb for a in plan_actions(confirm)}
        assert "remove" in verbs

    def test_modify_intent_has_no_remove(self):
        confirm = run_agent("update the KPI metrics")
        assert confirm["status"] == "ok"
        verbs = {a.verb for a in plan_actions(confirm)}
        assert "remove" not in verbs

    def test_actions_preserve_target_capability(self):
        confirm = run_agent("remove the KPI row")
        assert confirm["status"] == "ok"
        targets = {a.target_capability for a in plan_actions(confirm)}
        assert "presentation.kpi_row" in targets

    def test_actions_preserve_instance_hint(self):
        confirm = run_agent("remove the line chart")
        assert confirm["status"] == "ok"
        hints = [a.instance_hint for a in plan_actions(confirm) if a.instance_hint]
        assert any("linechart" in (h or "") for h in hints)


# ═══════════════════════════════════════════════════════════════════════════════
# B — Ambiguity: resolver-level (no apply_engine)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAmbiguityNoSilentFallback:
    """Resolver con multi-candidato → AmbiguousStructuralTargetError."""

    def test_b1_multi_candidate_raises(self):
        registry = StructuralRegistry()
        registry._register(
            component_instance_path="test.a.timeseries",
            type="Timeseries", capability="presentation.timeseries",
        )
        registry._register(
            component_instance_path="test.b.timeseries",
            type="Timeseries", capability="presentation.timeseries",
        )
        ir = _make_structural_ir([
            {"action": "CREATE", "target": "presentation.timeseries"},
        ])
        with pytest.raises(AmbiguousStructuralTargetError, match="Multiple candidates"):
            resolve(ir, registry, structural_index=StructuralIndex.empty())


# ═══════════════════════════════════════════════════════════════════════════════
# C — Delete authority gate unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeleteAuthorityGate:
    """_validate_delete_authority unit tests."""

    def test_no_delete_ops_passes(self):
        _validate_delete_authority(
            _make_structural_ir([{"action": "CREATE", "target": "presentation.kpi_row"}]),
            {"actions": []},
        )

    def test_delete_with_matching_action_passes(self):
        _validate_delete_authority(
            _make_structural_ir([{"action": "DELETE", "target": "presentation.kpi_row"}]),
            {"actions": [{"verb": "remove", "target_capability": "presentation.kpi_row"}]},
        )

    def test_delete_without_actions_raises(self):
        with pytest.raises(ValueError, match="DELETE operation"):
            _validate_delete_authority(
                _make_structural_ir([{"action": "DELETE", "target": "presentation.kpi_row"}]),
                {"actions": []},
            )

    def test_delete_with_wrong_verb_raises(self):
        with pytest.raises(ValueError, match="DELETE operation"):
            _validate_delete_authority(
                _make_structural_ir([{"action": "DELETE", "target": "presentation.kpi_row"}]),
                {"actions": [{"verb": "modify", "target_capability": "presentation.kpi_row"}]},
            )

    def test_delete_with_instance_hint_matches(self):
        _validate_delete_authority(
            _make_structural_ir([{"action": "DELETE", "target": "presentation.timeseries"}]),
            {"actions": [{"verb": "remove", "target_capability": "presentation.timeseries", "instance_hint": "linechart"}]},
        )

    def test_multiple_deletes_partial_match_raises(self):
        with pytest.raises(ValueError, match="DELETE operation for 'presentation.timeseries'"):
            _validate_delete_authority(
                _make_structural_ir([
                    {"action": "DELETE", "target": "presentation.kpi_row"},
                    {"action": "DELETE", "target": "presentation.timeseries"},
                ]),
                {"actions": [{"verb": "remove", "target_capability": "presentation.kpi_row"}]},
            )

    def test_mixed_ops_only_delete_checked(self):
        with pytest.raises(ValueError, match="DELETE operation"):
            _validate_delete_authority(
                _make_structural_ir([
                    {"action": "DELETE", "target": "presentation.kpi_row"},
                    {"action": "MODIFY", "target": "layout.page"},
                ]),
                {"actions": [{"verb": "modify", "target_capability": "layout.page"}]},
            )


# ═══════════════════════════════════════════════════════════════════════════════
# D — Structural invariants (no hidden paths from Phase 2)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase2DeletePathsRemoved:
    """Modulos y archivos de Phase 2 eliminados ya no existen.
    Depende de real disk truth — si Phase 3 introduce FS virtual,
    migrar a check de import."""

    def test_d1_deletion_module_gone(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("app.graphir.constraint.deletion")

    def test_d2_integration_test_gone(self):
        assert not os.path.exists(os.path.join(
            os.path.dirname(__file__), "..", "integration", "test_constraint_phase6a.py",
        ))

    def test_d3_unit_test_gone(self):
        assert not os.path.exists(os.path.join(
            os.path.dirname(__file__), "..", "unit", "test_constraint_deletion.py",
        ))

    def test_d4_sorted_fallback_replaced(self):
        registry = StructuralRegistry()
        registry._register(
            component_instance_path="test.a.kpi_row",
            type="KpiRow", capability="presentation.kpi_row",
        )
        registry._register(
            component_instance_path="test.b.kpi_row",
            type="KpiRow", capability="presentation.kpi_row",
        )
        ir = _make_structural_ir([
            {"action": "CREATE", "target": "presentation.kpi_row"},
        ])
        with pytest.raises(AmbiguousStructuralTargetError, match="Multiple candidates"):
            resolve(ir, registry, structural_index=StructuralIndex.empty())
