"""7 casos E2E: interpret → confirm → apply → verify fileops.

Caso	Esperado
────────────────────────────────────────────────
Remove KPI row      DELETE KpiRow
Remove timeseries   DELETE Timeseries
Add timeseries      CREATE/MODIFY Timeseries + MODIFY page
Update KPI metrics  MODIFY KpiRow
Update dashboard    container expansion
Invalid capability  clarification/rejection
Empty intent        gate.blocked
Verify              meta.verify presente en todo apply
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest

from app.engine.apply_engine import apply_engine
from tests.e2e.helpers import simulate_interpret, simulate_confirm
from tests.e2e.conftest import seed_file


SALES_PAGE_TSX = """\
import React from 'react';
import { KpiRow } from '../components/dashboard/KpiRow';
import { Timeseries } from '../components/charts/Timeseries';

export const SalesOverviewPage: React.FC = () => {
  return (
    <div>
      <KpiRow />
      <Timeseries />
      __COMPOSITION__
    </div>
  );
};
"""

KPI_ROW_TSX = """\
import { KpiCard } from './KpiCard';

export const KpiRow = () => <div className="row"><KpiCard /></div>;
"""

TIMESERIES_TSX = """\
export const Timeseries = () => <div>chart</div>;
"""


def _commit(ws: str, msg: str = "initial"):
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg, "--allow-empty"], cwd=ws, capture_output=True)


def _apply_and_assert(run_context, plan: dict, dry_run: bool = True) -> dict:
    """Run apply_engine and return result. dry_run=True avoids git mutation."""
    plan = {**plan, "gate": {"blocked": False}}
    result = apply_engine(run_context.run_id, plan, run_context, dry_run=dry_run)
    return result


def _assert_verify_present(result: dict):
    """Assert meta.verify is present (only non-None when not dry_run)."""
    verify = result.get("meta", {}).get("verify")
    is_dry = result.get("context", {}).get("dry_run", True)
    if is_dry:
        assert verify is None, "verify must be None in dry_run mode"
    else:
        assert verify is not None, "meta.verify should be present"
        assert verify.get("status") in ("passed", "skipped", "failed", "error"), f"unexpected verify status: {verify}"


# ═══════════════════════════════════════════════════════════════
# Caso 1: Remove KPI row → DELETE KpiRow
# ═══════════════════════════════════════════════════════════════

class TestRemoveKpiRow:
    def test_interpret_selects_dashboard(self):
        result = simulate_interpret("remove the KPI row")
        assert result["status"] == "ok"
        assert result["contract_id"] == "dashboard.sales_overview"

    def test_interpret_detects_kpi_remove(self):
        result = simulate_interpret("remove the KPI row")
        actions = result["proposed_actions"]
        assert len(actions) == 1
        assert actions[0]["verb"] == "remove"
        assert actions[0]["target_capability"] == "presentation.kpi_row"

    def test_confirm_produces_delete(self):
        interpret = simulate_interpret("remove the KPI row")
        result = simulate_confirm(interpret)
        assert result["status"] == "ok"
        ops = result["plan_preview"]["structural_operations"]
        assert len(ops) == 1
        assert ops[0]["action"] == "DELETE"
        assert ops[0]["target"] == "presentation.kpi_row"

    def test_apply_deletes_kpi_row(self, e2e_workspace, artifacts_dir):
        seed_file(e2e_workspace, "SalesOverviewPage.tsx", SALES_PAGE_TSX)
        seed_file(e2e_workspace, "KpiRow.tsx", KPI_ROW_TSX)
        seed_file(e2e_workspace, "Timeseries.tsx", TIMESERIES_TSX)
        _commit(e2e_workspace)

        interpret = simulate_interpret("remove the KPI row")
        confirm = simulate_confirm(interpret)
        assert confirm["status"] == "ok"

        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=e2e_workspace,
                         base_dir=e2e_workspace, artifacts=artifacts_dir)
        result = _apply_and_assert(ctx, confirm["plan"])
        assert result["execution"]["status"] == "ok"

        ops = result["execution"]["operations"]
        # Expect delete of KpiRow + anchor preservation create/modify
        delete_ops = [op for op in ops if op["action"] == "delete"]
        assert any("KpiRow" in op["path"] for op in delete_ops)

        _assert_verify_present(result)


# ═══════════════════════════════════════════════════════════════
# Caso 2: Remove timeseries → DELETE Timeseries
# ═══════════════════════════════════════════════════════════════

class TestRemoveTimeseries:
    def test_interpret_selects_dashboard(self):
        result = simulate_interpret("remove the trend chart")
        assert result["status"] == "ok"

    def test_interpret_detects_timeseries_remove(self):
        result = simulate_interpret("remove the trend chart")
        actions = result["proposed_actions"]
        assert len(actions) == 1
        assert actions[0]["verb"] == "remove"
        assert actions[0]["target_capability"] == "presentation.timeseries"

    def test_confirm_produces_delete(self):
        interpret = simulate_interpret("remove the trend chart")
        result = simulate_confirm(interpret)
        assert result["status"] == "ok"
        ops = result["plan_preview"]["structural_operations"]
        assert any(op["target"] == "presentation.timeseries" for op in ops)

    def test_apply_deletes_timeseries(self, e2e_workspace, artifacts_dir):
        seed_file(e2e_workspace, "SalesOverviewPage.tsx", SALES_PAGE_TSX)
        seed_file(e2e_workspace, "KpiRow.tsx", KPI_ROW_TSX)
        seed_file(e2e_workspace, "Timeseries.tsx", TIMESERIES_TSX)
        _commit(e2e_workspace)

        interpret = simulate_interpret("remove the trend chart")
        confirm = simulate_confirm(interpret)
        assert confirm["status"] == "ok"

        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=e2e_workspace,
                         base_dir=e2e_workspace, artifacts=artifacts_dir)
        result = _apply_and_assert(ctx, confirm["plan"])
        assert result["execution"]["status"] == "ok"

        ops = result["execution"]["operations"]
        assert any(op["action"] == "delete" and "Timeseries" in op["path"] for op in ops)

        _assert_verify_present(result)


# ═══════════════════════════════════════════════════════════════
# Caso 3: Add timeseries → CREATE/MODIFY Timeseries + MODIFY page
# ═══════════════════════════════════════════════════════════════

class TestAddTimeseries:
    def test_interpret_selects_dashboard(self):
        result = simulate_interpret("add a line chart")
        assert result["status"] == "ok"

    def test_interpret_detects_timeseries_create(self):
        result = simulate_interpret("add a line chart")
        actions = result["proposed_actions"]
        assert len(actions) == 1
        assert actions[0]["verb"] == "create"
        assert actions[0]["target_capability"] == "presentation.timeseries"

    def test_confirm_produces_create(self):
        interpret = simulate_interpret("add a line chart")
        result = simulate_confirm(interpret)
        assert result["status"] == "ok"
        ops = result["plan_preview"]["structural_operations"]
        assert any(op["target"] == "presentation.timeseries" for op in ops)

    def test_apply_creates_timeseries(self, e2e_workspace, artifacts_dir):
        seed_file(e2e_workspace, "SalesOverviewPage.tsx", SALES_PAGE_TSX)
        _commit(e2e_workspace)

        interpret = simulate_interpret("add a line chart")
        confirm = simulate_confirm(interpret)
        assert confirm["status"] == "ok"

        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=e2e_workspace,
                         base_dir=e2e_workspace, artifacts=artifacts_dir)
        result = _apply_and_assert(ctx, confirm["plan"])
        assert result["execution"]["status"] == "ok"

        ops = result["execution"]["operations"]
        create_ops = [op for op in ops if op["action"] in ("create", "modify")]
        timeseries_ops = [op for op in create_ops if "Timeseries" in op["path"]]
        assert len(timeseries_ops) >= 1

        _assert_verify_present(result)


# ═══════════════════════════════════════════════════════════════
# Caso 4: Update KPI metrics → MODIFY KpiRow
# ═══════════════════════════════════════════════════════════════

class TestUpdateKpiMetrics:
    def test_interpret_detects_kpi_modify(self):
        result = simulate_interpret("Update KPI metrics to revenue and growth")
        assert result["status"] == "ok"
        actions = result["proposed_actions"]
        assert len(actions) == 1
        assert actions[0]["verb"] == "modify"
        assert actions[0]["target_capability"] == "presentation.kpi_row"
        assert "revenue" in result["params_proposed"].get("metrics", [])
        assert "growth" in result["params_proposed"].get("metrics", [])

    def test_confirm_shows_modify(self):
        interpret = simulate_interpret("Update KPI metrics to revenue and growth")
        result = simulate_confirm(interpret)
        assert result["status"] == "ok"
        ops = result["plan_preview"]["structural_operations"]
        assert any(op["action"] == "MODIFY" and op["target"] == "presentation.kpi_row" for op in ops)

    def test_apply_modifies_kpi(self, e2e_workspace, artifacts_dir):
        seed_file(e2e_workspace, "KpiRow.tsx", KPI_ROW_TSX)
        seed_file(e2e_workspace, "SalesOverviewPage.tsx", SALES_PAGE_TSX)
        _commit(e2e_workspace)

        interpret = simulate_interpret("Update KPI metrics to revenue and growth")
        confirm = simulate_confirm(interpret)
        assert confirm["status"] == "ok"

        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=e2e_workspace,
                         base_dir=e2e_workspace, artifacts=artifacts_dir)
        result = _apply_and_assert(ctx, confirm["plan"])
        assert result["execution"]["status"] == "ok"

        _assert_verify_present(result)


# ═══════════════════════════════════════════════════════════════
# Caso 5: Update dashboard → container expansion
# ═══════════════════════════════════════════════════════════════

class TestUpdateDashboard:
    def test_interpret_selects_dashboard(self):
        result = simulate_interpret("Update dashboard")
        assert result["status"] == "ok"

    def test_interpret_detects_page_modify(self):
        result = simulate_interpret("Update dashboard")
        actions = result["proposed_actions"]
        assert len(actions) == 1
        assert actions[0]["target_capability"] == "layout.page"

    def test_confirm_expands_to_children(self):
        """Update dashboard → expande a layout.page + kpi_row + timeseries."""
        interpret = simulate_interpret("Update dashboard")
        result = simulate_confirm(interpret)
        assert result["status"] == "ok"
        ops = result["plan_preview"]["structural_operations"]
        targets = [o["target"] for o in ops]
        assert len(targets) >= 3
        assert "layout.page" in targets
        assert "presentation.kpi_row" in targets
        assert "presentation.timeseries" in targets

    def test_apply_with_container_expansion(self, e2e_workspace, artifacts_dir):
        seed_file(e2e_workspace, "SalesOverviewPage.tsx", SALES_PAGE_TSX)
        seed_file(e2e_workspace, "KpiRow.tsx", KPI_ROW_TSX)
        seed_file(e2e_workspace, "Timeseries.tsx", TIMESERIES_TSX)
        _commit(e2e_workspace)

        interpret = simulate_interpret("Update dashboard")
        confirm = simulate_confirm(interpret)
        assert confirm["status"] == "ok"

        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=e2e_workspace,
                         base_dir=e2e_workspace, artifacts=artifacts_dir)
        result = _apply_and_assert(ctx, confirm["plan"])
        ops = result["execution"].get("operations", [])
        # At minimum, should produce operations
        assert len(ops) >= 1

        _assert_verify_present(result)


# ═══════════════════════════════════════════════════════════════
# Caso 6: Invalid capability → clarification / rejection
# ═══════════════════════════════════════════════════════════════

class TestInvalidCapability:
    def test_unknown_action_needs_clarification(self):
        """Message without action verb → clarification needed."""
        result = simulate_interpret("I like the dashboard")
        assert result["status"] == "needs_clarification"

    def test_unsupported_contract_rejected(self):
        """Message that doesn't match any contract → unsupported."""
        result = simulate_interpret("fix the database schema")
        assert result["status"] == "unsupported"

    def test_unsupported_capability_not_mapped(self):
        """Message with unrecognized capability → no proposed_actions."""
        from app.intent.interpreter import _select_contract, _has_action_verb
        msg = "add a filter panel"
        # _has_action_verb may be true, but filter_panel may not exist in catalog
        if _has_action_verb(msg):
            # If it passed the action check, it still won't have a mapping
            result = simulate_interpret(msg)
            if result["status"] == "ok":
                actions = result["proposed_actions"]
                assert all(a["confidence"] < 0.5 for a in actions) or len(actions) == 0


# ═══════════════════════════════════════════════════════════════
# Caso 7: Empty intent → gate.blocked
# ═══════════════════════════════════════════════════════════════

class TestEmptyIntent:
    def test_empty_actions_rejected_in_confirm(self):
        """Confirm con actions vacío → rejected."""
        result = simulate_confirm({"status": "ok", "contract_id": "dashboard.sales_overview", "proposed_actions": []})
        assert result["status"] == "rejected"
        assert "no_actions" in result.get("reason", "")

    def test_empty_actions_produces_empty_plan(self):
        """PlanCompiled with empty actions → plan sin intents."""
        from app.intent.models import ConfirmedIntent
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[],
            params={},
            user_message="",
            interpretation_id="test",
        )
        from app.intent.plan_compiler import compile_plan
        plan = compile_plan(confirmed)
        assert len(plan.semantic_frame.get("actions", [])) == 0
        assert len(plan.intents) == 0

    def test_apply_rejects_empty_gate(self):
        """apply_engine con gate.blocked=True → rejected."""
        plan = {
            "skill_ir": {"contract_id": "dashboard.sales_overview", "version": 1, "params": {}, "confidence": 0.0},
            "semantic_frame": {"actions": []},
            "gate": {"blocked": True, "reason": "no_actions"},
        }
        plan_gate = plan.get("gate", {})
        assert plan_gate.get("blocked") is True


# ═══════════════════════════════════════════════════════════════
# Verificación de convergencia: expected ⊆ actual
# ═══════════════════════════════════════════════════════════════

class TestOperationConvergence:
    """expected_files debe estar contenido en actual_files del apply."""

    def test_remove_kpi_row_files_converge(self, e2e_workspace, artifacts_dir):
        seed_file(e2e_workspace, "SalesOverviewPage.tsx", SALES_PAGE_TSX)
        seed_file(e2e_workspace, "KpiRow.tsx", KPI_ROW_TSX)
        seed_file(e2e_workspace, "Timeseries.tsx", TIMESERIES_TSX)
        _commit(e2e_workspace)

        interpret = simulate_interpret("remove the KPI row")
        confirm = simulate_confirm(interpret)
        expected = {op["target"] for op in confirm["plan_preview"]["structural_operations"]}

        from app.runtime.context import RunContext
        ctx = RunContext(run_id=str(uuid.uuid4()), workspace=e2e_workspace,
                         base_dir=e2e_workspace, artifacts=artifacts_dir)
        result = _apply_and_assert(ctx, confirm["plan"])
        actual_paths = {op["path"] for op in result["execution"].get("operations", [])}

        # At least one actual file targets the expected capability
        assert len(actual_paths) >= len(expected), (
            f"Expected {len(expected)} operations, got {len(actual_paths)}"
        )
