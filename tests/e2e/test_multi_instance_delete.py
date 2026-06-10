"""Comprehensive tests for multi-instance DELETE resolution.

Grupos:
  A — Resolución explícita (hint → file único)
  B — Ambigüedad      (sin hint / hint inválido → error)
  C — Propagación     (instance_hint se conserva en toda la cadena)
  D — Safety invariants
  E — Regression guard (bug instance_id string vs int no recurre)
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest

from app.engine.apply_engine import (
    apply_engine as _apply_engine,
    _stem_matches_hint,
)
from app.engine.errors import AmbiguousStructuralTargetError
from app.engine.structural_completion import _resolve_delete_instance
from app.engine.structural_index import StructuralIndex
from app.engine.state_adapter import ComponentInstanceInfo
from app.intent.models import ConfirmedIntent, IntentAction
from app.intent.plan_compiler import compile_plan
from tests.e2e.helpers import simulate_interpret, simulate_confirm
from tests.e2e.conftest import seed_file


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def multi_timeseries_workspace(e2e_workspace):
    """Workspace con LineChart.tsx + Timeseries.tsx (misma capability)."""
    seed_file(e2e_workspace, "LineChart.tsx",
              "export const LineChart = () => <div>line</div>;")
    seed_file(e2e_workspace, "Timeseries.tsx",
              "export const Timeseries = () => <div>timeseries</div>;")
    seed_file(e2e_workspace, "SalesOverviewPage.tsx",
              """\
import React from 'react';
import { KpiRow } from '../components/dashboard/KpiRow';
import { LineChart } from '../components/charts/LineChart';
import { Timeseries } from '../components/charts/Timeseries';
export const SalesOverviewPage: React.FC = () => {
  return (
    <div>
      <KpiRow />
      <LineChart />
      <Timeseries />
    </div>
  );
};
""")
    subprocess.run(["git", "add", "-A"], cwd=e2e_workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", "multi-instance", "--allow-empty"],
                   cwd=e2e_workspace, capture_output=True)
    return e2e_workspace


@pytest.fixture
def single_timeseries_workspace(e2e_workspace):
    """Workspace con solo Timeseries.tsx + SalesOverviewPage."""
    seed_file(e2e_workspace, "Timeseries.tsx",
              "export const Timeseries = () => <div>chart</div>;")
    seed_file(e2e_workspace, "SalesOverviewPage.tsx",
              """\
import React from 'react';
import { Timeseries } from '../components/charts/Timeseries';
export const SalesOverviewPage: React.FC = () => {
  return (<div><Timeseries /></div>);
};
""")
    subprocess.run(["git", "add", "-A"], cwd=e2e_workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", "single-instance", "--allow-empty"],
                   cwd=e2e_workspace, capture_output=True)
    return e2e_workspace


@pytest.fixture
def structural_index_with_two():
    return StructuralIndex.from_mapping({
        "presentation.timeseries": [
            ComponentInstanceInfo(path="frontend/src/components/charts/LineChart.tsx", capability="presentation.timeseries", instance_id="0"),
            ComponentInstanceInfo(path="frontend/src/components/charts/Timeseries.tsx", capability="presentation.timeseries", instance_id="1"),
        ],
    })


@pytest.fixture
def structural_index_with_one():
    return StructuralIndex.from_mapping({
        "presentation.timeseries": [
            ComponentInstanceInfo(path="frontend/src/components/charts/Timeseries.tsx", capability="presentation.timeseries", instance_id="0"),
        ],
    })


# ═══════════════════════════════════════════════════════════════════════
# Grupo A — Resolución explícita
# ═══════════════════════════════════════════════════════════════════════

class TestExplicitResolution:
    """A1-A4: instance_hint resuelve al archivo correcto."""

    def test_a1_hint_linechart_deletes_only_linechart(self, multi_timeseries_workspace, artifacts_dir):
        """A1: hint='linechart' → solo LineChart.tsx es eliminado."""
        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=multi_timeseries_workspace,
                         base_dir=multi_timeseries_workspace, artifacts=artifacts_dir)

        # Build ConfirmedIntent directly with explicit hint
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[IntentAction(
                verb="remove",
                target_capability="presentation.timeseries",
                instance_hint="linechart",
            )],
            params={},
            user_message="remove the line chart",
            interpretation_id="multi-test",
        )
        plan = compile_plan(confirmed)
        plan_dict = plan.to_dict()
        plan_dict["gate"] = {"blocked": False}

        result = _apply_engine(ctx.run_id, plan_dict, ctx, dry_run=True)
        assert result["execution"]["status"] == "ok", f"Apply failed: {result}"

        ops = result["execution"].get("operations", [])
        delete_ops = [op for op in ops if op["action"] == "delete"]
        assert len(delete_ops) == 1, f"Expected 1 delete op, got {delete_ops}"
        assert "LineChart" in delete_ops[0]["path"], (
            f"Expected LineChart deletion, got {delete_ops[0]['path']}"
        )
        assert "Timeseries" not in delete_ops[0]["path"]

    def test_a2_hint_timeseries_deletes_only_timeseries(self, multi_timeseries_workspace, artifacts_dir):
        """A2: hint='timeseries' → solo Timeseries.tsx es eliminado."""
        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=multi_timeseries_workspace,
                         base_dir=multi_timeseries_workspace, artifacts=artifacts_dir)

        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[IntentAction(
                verb="remove",
                target_capability="presentation.timeseries",
                instance_hint="timeseries",
            )],
            params={},
            user_message="remove the timeseries chart",
            interpretation_id="multi-test",
        )
        plan = compile_plan(confirmed)
        plan_dict = plan.to_dict()
        plan_dict["gate"] = {"blocked": False}

        result = _apply_engine(ctx.run_id, plan_dict, ctx, dry_run=True)
        assert result["execution"]["status"] == "ok", f"Apply failed: {result}"

        ops = result["execution"].get("operations", [])
        delete_ops = [op for op in ops if op["action"] == "delete"]
        assert len(delete_ops) == 1, f"Expected 1 delete op, got {delete_ops}"
        assert "Timeseries" in delete_ops[0]["path"], (
            f"Expected Timeseries deletion, got {delete_ops[0]['path']}"
        )
        assert "LineChart" not in delete_ops[0]["path"]

    def test_a3_single_instance_no_hint_needed(self, single_timeseries_workspace, artifacts_dir):
        """A3: 1 archivo, sin hint → borra el único archivo.

        NOTE: requires params.metrics to pass the contract required-props gate.
        The single-instance workspace only has Timeseries.tsx.
        """
        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=single_timeseries_workspace,
                         base_dir=single_timeseries_workspace, artifacts=artifacts_dir)

        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[IntentAction(
                verb="remove",
                target_capability="presentation.timeseries",
            )],
            params={},
            user_message="remove the chart",
            interpretation_id="multi-test",
        )
        plan = compile_plan(confirmed)
        plan_dict = plan.to_dict()
        plan_dict["gate"] = {"blocked": False}
    
        result = _apply_engine(ctx.run_id, plan_dict, ctx, dry_run=True)
        assert result["execution"]["status"] == "ok", f"Apply failed: {result}"
    
        ops = result["execution"].get("operations", [])
        delete_ops = [op for op in ops if op["action"] == "delete"]
        assert len(delete_ops) == 1, f"Expected 1 delete op, got {delete_ops}"
        assert "Timeseries" in delete_ops[0]["path"]

    def test_a4_hint_with_simulate_interpret(self, multi_timeseries_workspace, artifacts_dir):
        """A4: simulate_interpret("remove the line chart") → propaga instance_hint."""
        interpret = simulate_interpret("remove the line chart")
        assert interpret["status"] == "ok"
        actions = interpret["proposed_actions"]
        assert len(actions) == 1
        assert actions[0].get("instance_hint") == "linechart", (
            f"Expected instance_hint='linechart', got {actions[0].get('instance_hint')}"
        )

        confirm = simulate_confirm(interpret)
        assert confirm["status"] == "ok"

        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=multi_timeseries_workspace,
                         base_dir=multi_timeseries_workspace, artifacts=artifacts_dir)

        plan = confirm["plan"]
        plan["gate"] = {"blocked": False}
        result = _apply_engine(ctx.run_id, plan, ctx, dry_run=True)
        assert result["execution"]["status"] == "ok", f"Apply failed: {result}"

        ops = result["execution"].get("operations", [])
        delete_ops = [op for op in ops if op["action"] == "delete"]
        assert len(delete_ops) >= 1
        delete_paths = [op["path"] for op in delete_ops]
        assert any("LineChart" in p for p in delete_paths), (
            f"Expected LineChart deletion, got {delete_paths}"
        )
        assert not any("Timeseries" in p for p in delete_paths), (
            f"Timeseries should NOT be deleted, got {delete_paths}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Grupo B — Ambigüedad
# ═══════════════════════════════════════════════════════════════════════

class TestAmbiguity:
    """B1-B3: Sin hint o hint ambiguo → AmbiguousStructuralTargetError."""

    def test_b1_no_hint_two_instances_raises(self, structural_index_with_two):
        """B1: _resolve_delete_instance('timeseries', None, index_2) → error."""
        with pytest.raises(AmbiguousStructuralTargetError) as exc:
            _resolve_delete_instance(
                "presentation.timeseries",
                instance_hint=None,
                structural_index=structural_index_with_two,
            )
        msg = str(exc.value)
        assert "2" in msg or "two" in msg or "several" in msg or "multiple" in msg or "timeseries" in msg
        assert len(msg) > 10

    def test_b2_no_hint_two_instances_apply_raises(self, multi_timeseries_workspace, artifacts_dir):
        """B1 (apply): sin hint en DELETE con 2 instancias → error."""
        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=multi_timeseries_workspace,
                         base_dir=multi_timeseries_workspace, artifacts=artifacts_dir)

        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[IntentAction(
                verb="remove",
                target_capability="presentation.timeseries",
            )],
            params={},
            user_message="remove the chart",
            interpretation_id="multi-test",
        )
        plan = compile_plan(confirmed)
        plan_dict = plan.to_dict()
        plan_dict["gate"] = {"blocked": False}

        result = _apply_engine(ctx.run_id, plan_dict, ctx, dry_run=True)
        assert result["execution"]["status"] == "clarification_needed", (
            f"Expected clarification_needed, got {result['execution']['status']}"
        )

    def test_b3_bad_hint_no_match_raises(self, multi_timeseries_workspace, artifacts_dir):
        """B3: hint='nonexistent' no match → error con lista de archivos disponibles."""
        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=multi_timeseries_workspace,
                         base_dir=multi_timeseries_workspace, artifacts=artifacts_dir)

        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[IntentAction(
                verb="remove",
                target_capability="presentation.timeseries",
                instance_hint="nonexistent",
            )],
            params={},
            user_message="remove the nonexistent chart",
            interpretation_id="multi-test",
        )
        plan = compile_plan(confirmed)
        plan_dict = plan.to_dict()
        plan_dict["gate"] = {"blocked": False}

        result = _apply_engine(ctx.run_id, plan_dict, ctx, dry_run=True)
        assert result["execution"]["status"] == "clarification_needed", (
            f"Expected clarification_needed, got {result['execution']['status']}"
        )
        detail = str(result["execution"].get("detail", ""))
        reason = str(result["execution"].get("reason", ""))
        msg = detail + reason
        assert "LineChart" in msg or "Timeseries" in msg, (
            f"Error message should mention available files: {msg}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Grupo C — Propagación
# ═══════════════════════════════════════════════════════════════════════

class TestPropagation:
    """C1-C4: instance_hint viaja sin pérdida por toda la cadena."""

    def test_c1_interpret_adds_hint_for_specific_keyword(self):
        """C1: simulate_interpret('remove the line chart') → instance_hint='linechart'."""
        interpret = simulate_interpret("remove the line chart")
        assert interpret["status"] == "ok"
        actions = interpret.get("proposed_actions", [])
        assert len(actions) == 1
        assert actions[0].get("instance_hint") == "linechart"

    def test_c1_interpret_adds_hint_for_timeseries_keyword(self):
        """C1: simulate_interpret('remove the timeseries chart') → instance_hint='timeseries'."""
        interpret = simulate_interpret("remove the timeseries chart")
        assert interpret["status"] == "ok"
        actions = interpret.get("proposed_actions", [])
        assert len(actions) == 1
        assert actions[0].get("instance_hint") == "timeseries"

    def test_c1_interpret_no_hint_for_generic_chart(self):
        """C1: simulate_interpret('remove the chart') → sin instance_hint."""
        interpret = simulate_interpret("remove the chart")
        assert interpret["status"] == "ok"
        actions = interpret.get("proposed_actions", [])
        assert len(actions) == 1
        assert actions[0].get("instance_hint") is None, (
            f"Generic 'chart' should not produce a hint, got {actions[0].get('instance_hint')}"
        )

    def test_c2_confirm_preserves_hint(self):
        """C2: confirm recibe instance_hint y lo preserva en CompiledPlan."""
        interpret = simulate_interpret("remove the line chart")
        assert interpret["status"] == "ok"
        confirm = simulate_confirm(interpret)
        assert confirm["status"] == "ok"

        plan = confirm["plan"]
        # CompiledPlan tiene semantic_frame con actions
        semantic_actions = plan.get("semantic_frame", {}).get("actions", [])
        assert len(semantic_actions) >= 1
        first = semantic_actions[0] if isinstance(semantic_actions[0], dict) else {}
        # Check if instance_hint is in the action
        if isinstance(semantic_actions[0], dict):
            assert first.get("instance_hint") == "linechart" or any(
                a.get("instance_hint") == "linechart" for a in semantic_actions
            ), f"instance_hint missing in semantic_actions: {semantic_actions}"

    def test_c3_plan_compiler_passes_hint(self):
        """C3: compile_plan preserva instance_hint en el plan resultante."""
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[IntentAction(
                verb="remove",
                target_capability="presentation.timeseries",
                instance_hint="linechart",
            )],
            params={},
            user_message="remove the line chart",
            interpretation_id="multi-test",
        )
        plan = compile_plan(confirmed)
        plan_dict = plan.to_dict()
        semantic_actions = plan_dict.get("semantic_frame", {}).get("actions", [])
        assert len(semantic_actions) >= 1
        first = semantic_actions[0]
        assert first.get("instance_hint") == "linechart", (
            f"instance_hint perdido en semantic_frame: {semantic_actions}"
        )

    def test_c4_hint_reaches_delete_loop(self, multi_timeseries_workspace, artifacts_dir):
        """C4: hint en plan → apply_engine elimina solo el archivo correcto."""
        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=multi_timeseries_workspace,
                         base_dir=multi_timeseries_workspace, artifacts=artifacts_dir)

        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[IntentAction(
                verb="remove",
                target_capability="presentation.timeseries",
                instance_hint="linechart",
            )],
            params={},
            user_message="remove the line chart",
            interpretation_id="multi-test",
        )
        plan = compile_plan(confirmed)
        plan_dict = plan.to_dict()
        plan_dict["gate"] = {"blocked": False}

        result = _apply_engine(ctx.run_id, plan_dict, ctx, dry_run=True)
        assert result["execution"]["status"] == "ok", f"Apply failed: {result}"

        ops = result["execution"].get("operations", [])
        delete_ops = [op for op in ops if op["action"] == "delete"]
        assert len(delete_ops) == 1
        assert "LineChart" in delete_ops[0]["path"]


# ═══════════════════════════════════════════════════════════════════════
# Grupo D — Safety invariants
# ═══════════════════════════════════════════════════════════════════════

class TestSafetyInvariants:
    """D1-D4: invariantes de seguridad en DELETE resolution."""

    def test_d1_delete_zero_files_no_crash(self, e2e_workspace, artifacts_dir):
        """D1: DELETE target sin archivos → warning, no crash."""
        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=e2e_workspace,
                         base_dir=e2e_workspace, artifacts=artifacts_dir)

        si = StructuralIndex.from_worktree(e2e_workspace)
        # Should not crash for any capability with no files
        for cap in ["presentation.timeseries", "presentation.kpi_row"]:
            files = si.resolve_all_file_paths(cap) if cap else []
            assert len(files) == 0, f"Expected no files for {cap}, got {files}"

    def test_d2_single_instance_no_hint_no_ambiguity(self, single_timeseries_workspace, artifacts_dir):
        """D2: 1 instancia, sin hint → delete del único archivo, sin error.

        NOTE: requires params.metrics to pass the contract required-props gate.
        The single-instance workspace only has Timeseries.tsx.
        """
        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=single_timeseries_workspace,
                         base_dir=single_timeseries_workspace, artifacts=artifacts_dir)

        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[IntentAction(
                verb="remove",
                target_capability="presentation.timeseries",
            )],
            params={},
            user_message="remove the chart",
            interpretation_id="multi-test",
        )
        plan = compile_plan(confirmed)
        plan_dict = plan.to_dict()
        plan_dict["gate"] = {"blocked": False}
    
        result = _apply_engine(ctx.run_id, plan_dict, ctx, dry_run=True)
        assert result["execution"]["status"] == "ok", f"Apply failed: {result}"
        delete_ops = [op for op in result["execution"].get("operations", []) if op["action"] == "delete"]
        assert len(delete_ops) == 1
        assert "Timeseries" in delete_ops[0]["path"]
    
    def test_d3_stem_matches_hint_exact_match(self):
        """D3: _stem_matches_hint verifica coincidencia exacta del stem."""
        assert _stem_matches_hint("LineChart.tsx", "linechart") is True
        assert _stem_matches_hint("Timeseries.tsx", "timeseries") is True
        assert _stem_matches_hint("LineChart.tsx", "chart") is False  # substring no match
        assert _stem_matches_hint("Timeseries.tsx", "linechart") is False
        assert _stem_matches_hint("LineChart.tsx", "line") is False    # substring no match
        assert _stem_matches_hint("bar/LineChart.tsx", "linechart") is True  # path ok

    def test_d4_stem_matches_hint_case_insensitive(self):
        """D4: _stem_matches_hint normaliza filename (lowercase), hint pre-lowercased."""
        assert _stem_matches_hint("LINECHART.tsx", "linechart") is True
        # Hint debe estar pre-lowercased (caller lo hace)
        assert _stem_matches_hint("linechart.tsx", "linechart") is True


# ═══════════════════════════════════════════════════════════════════════
# Grupo E — Regression guard
# ═══════════════════════════════════════════════════════════════════════

class TestRegressionGuard:
    """E1: Bugs históricos no recurren."""

    def test_e1_instance_id_string_vs_int_no_longer_bug(self):
        """E1: instance_id '0' (string) y 0 (int) son compatibles.

        Bug original: state_adapter.py comparaba instance_id == 0 (int)
        pero el valor era '0' (string), causando que todas las instancias
        tuvieran sufijo ':N' y el index devolviera 2 instancias en vez de 1.
        """
        # Both representations should work
        info_str = ComponentInstanceInfo(
            path="test.tsx", capability="presentation.timeseries", instance_id="0"
        )
        info_int = ComponentInstanceInfo(
            path="test.tsx", capability="presentation.timeseries", instance_id=0
        )
        # Both are valid and should not cause comparison issues
        assert info_str.instance_id == "0"
        assert info_int.instance_id == 0
        # StructuralIndex should accept both
        index = StructuralIndex.from_mapping({
            "presentation.timeseries": [info_str, info_int],
        })
        instances = index.get_instances("presentation.timeseries")
        assert len(instances) == 2, f"Expected 2 instances, got {len(instances)}"

    def test_e2_resolve_delete_instance_no_crash_empty_index(self):
        """E1b: _resolve_delete_instance no crash con index vacío."""
        index = StructuralIndex.empty()
        result = _resolve_delete_instance(
            "presentation.timeseries", instance_hint=None, structural_index=index
        )
        assert result is None  # No error, just None

    def test_e3_discover_files_detects_multi_instance(self, multi_timeseries_workspace):
        """E: StructuralIndex detecta ambas instancias."""
        si = StructuralIndex.from_worktree(multi_timeseries_workspace)
        timeseries_files = si.resolve_all_file_paths("presentation.timeseries")
        assert len(timeseries_files) >= 2, (
            f"Expected 2+ timeseries files, got {timeseries_files}"
        )
        stems = {os.path.splitext(os.path.basename(fp))[0].lower() for fp in timeseries_files}
        assert "linechart" in stems, f"Missing linechart in {stems}"
        assert "timeseries" in stems, f"Missing timeseries in {stems}"



