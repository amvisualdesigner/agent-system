"""E2E Composition Audit — CREATE, DELETE, MODIFY, REPLACE.

Verifica el estado real del filesystem después de apply_engine(dry_run=False):
archivos creados/eliminados, contenido generado, composición actualizada.

Cada escenario usa un workspace Git sembrado con contenido realista
y assertions sobre el disco, no sobre el dict de resultado.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid

import pytest

from app.config.settings import settings
from app.intent.models import ConfirmedIntent, IntentAction
from app.intent.plan_compiler import compile_plan


# ── Helpers ──────────────────────────────────────────────────────────

def _component_path(workspace: str, *parts: str) -> str:
    return os.path.join(workspace, "src", "pages", "dashboard", *parts)


def _read_file(path: str) -> str:
    with open(path) as f:
        return f.read()


def _git_diff_head_stat(workspace: str) -> str:
    result = subprocess.run(
        ["git", "diff", "HEAD~1", "--stat"],
        cwd=workspace, capture_output=True, text=True,
    )
    return result.stdout


def _run_apply(workspace: str, confirmed: ConfirmedIntent) -> dict:
    """Run apply_engine with dry_run=False and return result dict."""
    from app.engine.apply_engine import apply_engine
    from app.runtime.context import RunContext

    plan = compile_plan(confirmed)
    artifacts_tmp = tempfile.mkdtemp(prefix="audit_artifacts_", dir=settings.ARTIFACTS_DIR)
    ctx = RunContext(
        run_id=str(uuid.uuid4()),
        base_dir=workspace,
        workspace=workspace,
        artifacts=artifacts_tmp,
    )
    try:
        return apply_engine(ctx.run_id, plan.to_dict(), ctx, dry_run=False)
    finally:
        shutil.rmtree(artifacts_tmp, ignore_errors=True)


# ── CREATE: "add a KPI row to the dashboard" ────────────────────────
# Workspace starts WITHOUT KpiRow.tsx — CREATE must generate it.

CREATE_ACTIONS = [
    IntentAction(
        verb="create",
        target_capability="presentation.kpi_row",
        params={"metrics": ["revenue", "growth"]},
    ),
]


class TestCompositionCreate:
    """CREATE — archivo creado desde capability del contrato."""

    def test_file_created(self, create_workspace):
        kpi_path = _component_path(create_workspace, "components", "KpiRow.tsx")
        assert not os.path.exists(kpi_path), "Precondition: KpiRow should NOT exist"

        result = _run_apply(create_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=CREATE_ACTIONS,
            params={"metrics": ["revenue", "growth"]},
            user_message="add a KPI row to the dashboard",
            interpretation_id="audit-create",
        ))
        assert result["execution"]["status"] in ("ok", "verify_failed"), (
            f"CREATE should succeed, got {result['execution']['status']}"
        )
        assert os.path.exists(kpi_path), (
            f"KpiRow.tsx should exist after CREATE: {kpi_path}"
        )

    def test_file_content_has_component_markers(self, create_workspace):
        _run_apply(create_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=CREATE_ACTIONS,
            params={"metrics": ["revenue", "growth"]},
            user_message="add a KPI row to the dashboard",
            interpretation_id="audit-create-content",
        ))
        kpi_path = _component_path(create_workspace, "components", "KpiRow.tsx")
        content = _read_file(kpi_path)
        assert len(content) > 50, "File content should be substantial"
        assert "__COMPOSITION__" not in content, "No composition placeholder should remain"
        assert "KpiRow" in content, "File should reference its own component name"

    def test_git_diff_shows_file_created(self, create_workspace):
        _run_apply(create_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=CREATE_ACTIONS,
            params={"metrics": ["revenue", "growth"]},
            user_message="add a KPI row to the dashboard",
            interpretation_id="audit-create-diff",
        ))
        diff_stat = _git_diff_head_stat(create_workspace)
        assert diff_stat, "git diff HEAD~1 --stat should show changes"
        assert "KpiRow" in diff_stat, "Diff should reference the new file"


# ── DELETE: "remove the line chart" ─────────────────────────────────

DELETE_ACTIONS = [
    IntentAction(
        verb="remove",
        target_capability="presentation.timeseries",
        instance_hint="linechart",
    ),
]


class TestCompositionDelete:
    """DELETE — archivo eliminado, composición actualizada."""

    def test_file_deleted(self, composition_workspace):
        linechart_path = _component_path(composition_workspace, "components", "LineChart.tsx")
        assert os.path.exists(linechart_path), "Precondition: LineChart.tsx should exist"

        result = _run_apply(composition_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=DELETE_ACTIONS,
            params={},
            user_message="remove the line chart",
            interpretation_id="audit-delete",
        ))
        assert result["execution"]["status"] in ("ok", "verify_failed"), (
            f"DELETE should succeed, got {result['execution']['status']}"
        )
        assert not os.path.exists(linechart_path), (
            f"LineChart.tsx should be deleted: {linechart_path}"
        )

    def test_other_components_preserved(self, composition_workspace):
        kpirow_path = _component_path(composition_workspace, "components", "KpiRow.tsx")
        timeseries_path = _component_path(composition_workspace, "components", "Timeseries.tsx")
        assert os.path.exists(kpirow_path), "Precondition: KpiRow.tsx should exist"

        _run_apply(composition_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=DELETE_ACTIONS,
            params={},
            user_message="remove the line chart",
            interpretation_id="audit-delete-other",
        ))
        assert os.path.exists(kpirow_path), "KpiRow should survive DELETE"
        assert os.path.exists(timeseries_path), "Timeseries should survive DELETE"

    def test_git_diff_shows_file_deleted(self, composition_workspace):
        _run_apply(composition_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=DELETE_ACTIONS,
            params={},
            user_message="remove the line chart",
            interpretation_id="audit-delete-diff",
        ))
        diff_stat = _git_diff_head_stat(composition_workspace)
        assert diff_stat, "git diff HEAD~1 --stat should show changes"
        assert "LineChart" in diff_stat, "Diff should reference the deleted file"


# ── MODIFY: "update KPI metrics to show revenue and growth" ─────────

MODIFY_ACTIONS = [
    IntentAction(
        verb="modify",
        target_capability="presentation.kpi_row",
        params={"metrics": ["revenue", "growth"]},
    ),
]


class TestCompositionModify:
    """MODIFY — mismo archivo, contenido cambiado."""

    def test_same_file_modified(self, composition_workspace):
        kpi_path = _component_path(composition_workspace, "components", "KpiRow.tsx")
        original_content = _read_file(kpi_path)

        result = _run_apply(composition_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=MODIFY_ACTIONS,
            params={"metrics": ["revenue", "growth"]},
            user_message="update KPI metrics to show revenue and growth",
            interpretation_id="audit-modify",
        ))
        assert result["execution"]["status"] in ("ok", "verify_failed"), (
            f"MODIFY should succeed, got {result['execution']['status']}"
        )

        new_content = _read_file(kpi_path)
        assert new_content != original_content, "Content should change after MODIFY"

    def test_file_has_expected_markers(self, composition_workspace):
        _run_apply(composition_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=MODIFY_ACTIONS,
            params={"metrics": ["revenue", "growth"]},
            user_message="update KPI metrics to show revenue and growth",
            interpretation_id="audit-modify-markers",
        ))
        kpi_path = _component_path(composition_workspace, "components", "KpiRow.tsx")
        content = _read_file(kpi_path)
        assert "__COMPOSITION__" not in content, "No composition placeholder"
        assert "KpiRowProps" in content or "KpiRow" in content, "Component name present"
        assert len(content) > 50, "Substantial content"

    def test_git_diff_shows_one_file_modified(self, composition_workspace):
        _run_apply(composition_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=MODIFY_ACTIONS,
            params={"metrics": ["revenue", "growth"]},
            user_message="update KPI metrics to show revenue and growth",
            interpretation_id="audit-modify-diff",
        ))
        diff_stat = _git_diff_head_stat(composition_workspace)
        assert diff_stat, "git diff HEAD~1 --stat should show changes"
        assert "KpiRow" in diff_stat, "Diff should reference KpiRow"


# ── REPLACE: "replace timeseries with KPI row" ──────────────────────
# Workspace starts WITHOUT KpiRow.tsx — REPLACE creates it and
# redirects Page.tsx imports from Timeseries to KpiRow.

REPLACE_ACTIONS = [
    IntentAction(
        verb="replace",
        target_capability="presentation.timeseries",
        source_capability="presentation.kpi_row",
        params={},
    ),
]


class TestCompositionReplace:
    """REPLACE — source preservado (KEEP), target creado, imports redirigidos."""

    def test_source_preserved_and_target_created(self, replace_workspace):
        linechart_path = _component_path(replace_workspace, "components", "LineChart.tsx")
        timeseries_path = _component_path(replace_workspace, "components", "Timeseries.tsx")
        kpi_path = _component_path(replace_workspace, "components", "KpiRow.tsx")

        assert os.path.exists(linechart_path), "Precondition: LineChart should exist"
        assert os.path.exists(timeseries_path), "Precondition: Timeseries should exist"
        assert not os.path.exists(kpi_path), "Precondition: KpiRow should NOT exist"

        _run_apply(replace_workspace, ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=REPLACE_ACTIONS,
            params={},
            user_message="replace timeseries with KPI row",
            interpretation_id="audit-replace",
        ))

        assert os.path.exists(linechart_path), "LineChart should be preserved (KEEP)"
        assert os.path.exists(timeseries_path), "Timeseries should be preserved (KEEP)"
        assert os.path.exists(kpi_path), "KpiRow should be created by SubstitutionOp"

    def test_lifecycle_is_keep(self):
        """Verificar que el lifecycle de la capability source es KEEP, no DELETE."""
        from app.engine.structural_completion import complete_structure
        from app.contracts.semantic_resolution import SemanticResolution
        from app.contracts.contract_resolution import ContractResolution
        from app.contracts.skill_registry import get_contract

        contract = get_contract("dashboard.sales_overview", 1)
        assert contract is not None

        struktural = complete_structure(
            semantic_resolution=SemanticResolution(
                actions=[{"verb": "replace", "object": "timeseries", "reference": "kpi"}],
                semantic_params={},
                semantic_provenance={},
                confidence=1.0,
            ),
            contract_resolution=ContractResolution(
                contract_params={},
                contract_provenance={},
                confidence=1.0,
            ),
            contract=contract,
        )
        ops = struktural.operations
        ts_ops = [o for o in ops if o.get("target") == "presentation.timeseries"]
        assert len(ts_ops) == 1
        assert ts_ops[0]["action"] == "KEEP", (
            f"REPLACE should produce KEEP lifecycle, got {ts_ops[0]['action']}"
        )

    def test_substitution_op_created(self):
        """Verificar que SubstitutionOp se crea para el par source→target."""
        from app.engine.structural_completion import complete_structure
        from app.contracts.semantic_resolution import SemanticResolution
        from app.contracts.contract_resolution import ContractResolution
        from app.contracts.skill_registry import get_contract

        contract = get_contract("dashboard.sales_overview", 1)
        assert contract is not None

        struktural = complete_structure(
            semantic_resolution=SemanticResolution(
                actions=[{"verb": "replace", "object": "timeseries", "reference": "kpi"}],
                semantic_params={},
                semantic_provenance={},
                confidence=1.0,
            ),
            contract_resolution=ContractResolution(
                contract_params={},
                contract_provenance={},
                confidence=1.0,
            ),
            contract=contract,
        )
        assert len(struktural.substitutions) > 0, "REPLACE should produce SubstitutionOps"
        found = any(
            s.source == "presentation.timeseries" and s.target == "presentation.kpi_row"
            for s in struktural.substitutions
        )
        assert found, (
            f"Should find SubstitutionOp(timeseries→kpi_row), "
            f"got: {[(s.source, s.target) for s in struktural.substitutions]}"
        )
