"""Integration E2E tests against the real agent-test-repo.

Ejercita StructuralIndex.from_worktree(), complete_structure(), y
apply_engine() contra el repositorio real en lugar de from_mapping().

Requisito: /opt/agent-repos/agent-test-repo debe existir.
Estos tests copian el repo a un temp dir — nunca mutan el original.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid

import pytest

from app.engine.structural_index import StructuralIndex
from app.engine.structural_completion import complete_structure
from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.contract_resolution import ContractResolution
from app.contracts.skill_registry import get_contract
from app.config.settings import settings


AGENT_TEST_REPO = "/opt/agent-repos/agent-test-repo"


@pytest.fixture(scope="module")
def agent_test_repo_copy():
    """Crear un temp copy del agent-test-repo con git init."""
    if not os.path.isdir(AGENT_TEST_REPO):
        pytest.skip("agent-test-repo not available at " + AGENT_TEST_REPO)

    tmpdir = tempfile.mkdtemp(prefix="agent_repo_int_", dir=settings.RUNS_DIR)
    src = os.path.join(AGENT_TEST_REPO, "frontend")
    dst = os.path.join(tmpdir, "frontend")
    shutil.copytree(src, dst, symlinks=True, ignore_dangling_symlinks=True)

    # Re-inicializar git en el temp dir
    subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)

    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestStructuralIndexFromRealRepo:
    """StructuralIndex.from_worktree() contra el agent-test-repo real."""

    def test_index_discovers_capabilities(self, agent_test_repo_copy):
        idx = StructuralIndex.from_worktree(agent_test_repo_copy)
        assert not idx.is_empty, "Index should discover capabilities"
        assert "presentation.kpi_row" in idx, "Should find KpiRow"
        assert "layout.page" in idx, "Should find SalesOverviewPage → layout.page"
        assert "presentation.table" in idx, "Should find DataTable"

    def test_index_finds_timeseries_multi_instance(self, agent_test_repo_copy):
        """Timeseries.tsx y LineChart.tsx → multi-instance presentation.timeseries."""
        idx = StructuralIndex.from_worktree(agent_test_repo_copy)
        assert "presentation.timeseries" in idx, "Should find Timeseries/LineChart"
        instances = idx.get_instances("presentation.timeseries")
        assert len(instances) >= 2, (
            f"Expected >=2 instances (Timeseries + LineChart), got {len(instances)}"
        )

    def test_index_finds_bar_chart(self, agent_test_repo_copy):
        idx = StructuralIndex.from_worktree(agent_test_repo_copy)
        assert "presentation.chart.bar" in idx, "Should find BarChart"

    def test_index_resolve_paths_are_relative(self, agent_test_repo_copy):
        idx = StructuralIndex.from_worktree(agent_test_repo_copy)
        paths = idx.resolve_all_paths("presentation.kpi_row")
        assert len(paths) == 1
        assert "kpi_row" in paths[0]

    def test_index_resolve_nonexistent_returns_empty(self, agent_test_repo_copy):
        idx = StructuralIndex.from_worktree(agent_test_repo_copy)
        assert idx.resolve_path("presentation.filter_panel") is None
        assert idx.resolve_all_paths("presentation.filter_panel") == []


class TestCompleteStructureWithRealRepo:
    """complete_structure() contra StructuralIndex real del agent-test-repo."""

    def test_remove_kpi_row_lifecycle(self, agent_test_repo_copy):
        """remove kpi_row → DELETE en el lifecycle."""
        idx = StructuralIndex.from_worktree(agent_test_repo_copy)
        assert idx.exists("presentation.kpi_row")

        sem = SemanticResolution(
            actions=[{"verb": "remove", "object": "kpi", "confidence": 1.0}],
            semantic_params={},
            semantic_provenance={},
            confidence=1.0,
        )
        cr = ContractResolution(
            contract_params={},
            contract_provenance={},
            confidence=1.0,
        )
        contract = get_contract("dashboard.sales_overview", 1)
        assert contract is not None

        struktural = complete_structure(
            semantic_resolution=sem,
            contract_resolution=cr,
            contract=contract,
            structural_index=idx,
        )

        ops = struktural.operations
        kpi_ops = [o for o in ops if o.get("target") == "presentation.kpi_row"]
        assert len(kpi_ops) == 1
        assert kpi_ops[0]["action"] == "DELETE"

    def test_create_timeseries_lifecycle(self, agent_test_repo_copy):
        """create timeseries → MODIFY en el lifecycle (ya existe)."""
        idx = StructuralIndex.from_worktree(agent_test_repo_copy)
        assert idx.exists("presentation.timeseries")

        sem = SemanticResolution(
            actions=[{"verb": "create", "object": "line", "confidence": 1.0}],
            semantic_params={},
            semantic_provenance={},
            confidence=1.0,
        )
        cr = ContractResolution(
            contract_params={},
            contract_provenance={},
            confidence=1.0,
        )
        contract = get_contract("dashboard.sales_overview", 1)
        assert contract is not None

        struktural = complete_structure(
            semantic_resolution=sem,
            contract_resolution=cr,
            contract=contract,
            structural_index=idx,
        )

        ops = struktural.operations
        ts_ops = [o for o in ops if o.get("target") == "presentation.timeseries"]
        assert len(ts_ops) == 1
        assert ts_ops[0]["action"] == "MODIFY"

    def test_modify_dashboard_expands_to_container(self, agent_test_repo_copy):
        """modify dashboard → expande a hijos del contrato."""
        idx = StructuralIndex.from_worktree(agent_test_repo_copy)

        sem = SemanticResolution(
            actions=[{"verb": "modify", "object": "dashboard", "confidence": 1.0}],
            semantic_params={},
            semantic_provenance={},
            confidence=1.0,
        )
        cr = ContractResolution(
            contract_params={},
            contract_provenance={},
            confidence=1.0,
        )
        contract = get_contract("dashboard.sales_overview", 1)
        assert contract is not None

        struktural = complete_structure(
            semantic_resolution=sem,
            contract_resolution=cr,
            contract=contract,
            structural_index=idx,
        )

        ops = struktural.operations
        targets = {o["target"] for o in ops}
        assert "layout.page" in targets
        assert "presentation.kpi_row" in targets
        assert "presentation.timeseries" in targets


class TestVerifyWithRealRepo:
    """verify_worktree contra el agent-test-repo real (con node_modules)."""

    def test_verify_passes_on_clean_repo(self, agent_test_repo_copy):
        from app.engine.verify_worktree import verify_worktree
        result = verify_worktree(agent_test_repo_copy)
        assert result["status"] in ("passed", "skipped"), (
            f"Expected passed/skipped, got {result['status']}: {result}"
        )
        if result["status"] == "passed":
            assert result["check"] in ("tsc", "npm_run_build")

    def test_verify_after_modify_timeseries(self, agent_test_repo_copy):
        """Run apply_engine + verify contra el repo real."""
        from app.engine.apply_engine import apply_engine
        from app.runtime.context import RunContext
        from app.intent.models import ConfirmedIntent, IntentAction
        from app.intent.plan_compiler import compile_plan

        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(verb="modify", target_capability="presentation.kpi_row", params={"metrics": ["revenue", "growth"]}),
            ],
            params={"metrics": ["revenue", "growth"]},
            user_message="Update KPI metrics to revenue and growth",
            interpretation_id="int-test",
        )
        plan = compile_plan(confirmed)

        artifacts_tmp = tempfile.mkdtemp(prefix="artifacts_int_", dir=settings.ARTIFACTS_DIR)
        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            base_dir=agent_test_repo_copy,
            workspace=agent_test_repo_copy,
            artifacts=artifacts_tmp,
        )

        try:
            result = apply_engine(ctx.run_id, plan.to_dict(), ctx, dry_run=False)
            assert result["execution"]["status"] in ("ok", "verify_failed"), (
                f"Expected ok/verify_failed, got {result['execution']['status']}"
            )
            verify = result.get("meta", {}).get("verify")
            assert verify is not None, "meta.verify should be present"
            assert verify.get("status") in ("passed", "skipped", "failed", "error"), (
                f"unexpected verify status: {verify}"
            )
        finally:
            shutil.rmtree(artifacts_tmp, ignore_errors=True)
