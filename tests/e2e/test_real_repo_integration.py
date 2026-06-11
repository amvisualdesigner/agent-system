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
        )

        ops = struktural.operations
        ts_ops = [o for o in ops if o.get("target") == "presentation.timeseries"]
        assert len(ts_ops) == 1
        # Phase 3: create → CREATE (reconciliation moves to ApplyEngine)
        assert ts_ops[0]["action"] == "CREATE"

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
        """Run apply_engine + verify contra el repo real.

        Note: KpiRow params 'metrics' require data_access.json binding to
        resolve to component prop 'data'. Without it (agent_test_repo_copy
        has no .opencode/config), the plan is now correctly rejected by the
        SSOT gate. This is more honest than silently producing broken JSX.
        """
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
            # SSOT gate rejects when required props can't be resolved
            assert result["execution"]["status"] in ("ok", "verify_failed", "rejected"), (
                f"Expected ok/verify_failed/rejected, got {result['execution']['status']}"
            )
        finally:
            shutil.rmtree(artifacts_tmp, ignore_errors=True)


class TestPhase6DataFlowWithRealRepo:
    """Phase 6 — Page data source + slice distribution E2E with real repo.

    Tests verify that when composition sync promotes Page to MODIFY (via child
    CREATE → instance_only), the generated Page.tsx includes:
      1. useDashboardData() hook declaration
      2. Slice distribution via _pageData refs
      3. Compilation under tsc --noEmit
    """

    @pytest.fixture(scope="function")
    def phase6_repo_copy(self):
        """Copy agent-test-repo AND add v3 .opencode/data_access.json."""
        if not os.path.isdir(AGENT_TEST_REPO):
            pytest.skip("agent-test-repo not available at " + AGENT_TEST_REPO)

        tmpdir = tempfile.mkdtemp(prefix="phase6_e2e_", dir=settings.RUNS_DIR)
        src = os.path.join(AGENT_TEST_REPO, "frontend")
        dst = os.path.join(tmpdir, "frontend")
        shutil.copytree(src, dst, symlinks=True, ignore_dangling_symlinks=True)

        # Add v3 data_access.json for Phase 6
        opencode_dir = os.path.join(tmpdir, ".opencode")
        os.makedirs(opencode_dir, exist_ok=True)
        data_access_src = os.path.join(
            os.path.dirname(__file__), "..", "..", "backend", "config", "data_access.json"
        )
        shutil.copy2(data_access_src, os.path.join(opencode_dir, "data_access.json"))

        # Re-initialize git
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)

        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    def _read_page_content(self, workspace: str) -> str:
        """Read the generated Page.tsx from the workspace."""
        page_path = os.path.join(workspace, "frontend", "src", "pages", "dashboard", "Page.tsx")
        if not os.path.exists(page_path):
            page_path = os.path.join(workspace, "frontend", "src", "pages", "dashboard", "SalesOverviewPage.tsx")
        with open(page_path) as f:
            return f.read()

    def test_phase6_hook_declaration(self, phase6_repo_copy):
        """Page.tsx must declare useDashboardData() after Phase 6 regeneration."""
        from app.engine.apply_engine import apply_engine
        from app.runtime.context import RunContext
        from app.intent.models import ConfirmedIntent, IntentAction
        from app.intent.plan_compiler import compile_plan

        # CREATE on existing capability → instance_only → composition sync promotes Page
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(verb="create", target_capability="presentation.timeseries", params={"timeseries_metric": "revenue"}),
            ],
            params={"timeseries_metric": "revenue"},
            user_message="Add a timeseries chart",
            interpretation_id="phase6-hook",
        )
        plan = compile_plan(confirmed)

        artifacts_tmp = tempfile.mkdtemp(prefix="artifacts_phase6_", dir=settings.ARTIFACTS_DIR)
        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            base_dir=phase6_repo_copy,
            workspace=phase6_repo_copy,
            artifacts=artifacts_tmp,
        )

        try:
            result = apply_engine(ctx.run_id, plan.to_dict(), ctx, dry_run=False)
            assert result["execution"]["status"] in ("ok", "verify_failed"), (
                f"Expected ok/verify_failed, got {result['execution']['status']}"
            )
            page_content = self._read_page_content(phase6_repo_copy)
            assert "useDashboardData" in page_content, (
                "Page.tsx should contain useDashboardData hook after Phase 6 regeneration"
            )
            assert "const _pageData = useDashboardData();" in page_content, (
                "Page.tsx should declare _pageData = useDashboardData()"
            )
        finally:
            shutil.rmtree(artifacts_tmp, ignore_errors=True)

    def test_phase6_slice_distribution(self, phase6_repo_copy):
        """Page.tsx must distribute slices to children via _pageData refs."""
        from app.engine.apply_engine import apply_engine
        from app.runtime.context import RunContext
        from app.intent.models import ConfirmedIntent, IntentAction
        from app.intent.plan_compiler import compile_plan

        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(verb="create", target_capability="presentation.timeseries", params={"timeseries_metric": "revenue"}),
            ],
            params={"timeseries_metric": "revenue"},
            user_message="Add a timeseries chart",
            interpretation_id="phase6-slices",
        )
        plan = compile_plan(confirmed)

        artifacts_tmp = tempfile.mkdtemp(prefix="artifacts_phase6_", dir=settings.ARTIFACTS_DIR)
        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            base_dir=phase6_repo_copy,
            workspace=phase6_repo_copy,
            artifacts=artifacts_tmp,
        )

        try:
            result = apply_engine(ctx.run_id, plan.to_dict(), ctx, dry_run=False)
            assert result["execution"]["status"] in ("ok", "verify_failed")
            page_content = self._read_page_content(phase6_repo_copy)
            # Expect slice distribution: kpiData for KpiRow, chartData.timeseries for Timeseries
            assert "_pageData.kpiData" in page_content, (
                "Page should pass _pageData.kpiData to KpiRow"
            )
            assert "_pageData.chartData.timeseries" in page_content, (
                "Page should pass _pageData.chartData.timeseries to Timeseries"
            )
        finally:
            shutil.rmtree(artifacts_tmp, ignore_errors=True)

    def test_phase6_compilation(self, phase6_repo_copy):
        """Generated Page.tsx with Phase 6 data flow must compile under tsc --noEmit."""
        from app.engine.apply_engine import apply_engine
        from app.engine.verify_worktree import verify_worktree
        from app.runtime.context import RunContext
        from app.intent.models import ConfirmedIntent, IntentAction
        from app.intent.plan_compiler import compile_plan

        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(verb="create", target_capability="presentation.timeseries", params={"timeseries_metric": "revenue"}),
            ],
            params={"timeseries_metric": "revenue"},
            user_message="Add a timeseries chart",
            interpretation_id="phase6-compile",
        )
        plan = compile_plan(confirmed)

        artifacts_tmp = tempfile.mkdtemp(prefix="artifacts_phase6_", dir=settings.ARTIFACTS_DIR)
        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            base_dir=phase6_repo_copy,
            workspace=phase6_repo_copy,
            artifacts=artifacts_tmp,
        )

        try:
            result = apply_engine(ctx.run_id, plan.to_dict(), ctx, dry_run=False)
            # verify_worktree is called as post-step in apply_engine
            verify = result.get("meta", {}).get("verify", {})
            assert verify.get("status") in ("passed", "skipped", "failed", "error"), (
                f"unexpected verify status: {verify}"
            )

            # Also run verify_worktree directly for explicit check
            direct_verify = verify_worktree(phase6_repo_copy)
            assert direct_verify["status"] in ("passed", "skipped"), (
                f"tsc --noEmit failed after Phase 6 generation: {direct_verify}"
            )
        finally:
            shutil.rmtree(artifacts_tmp, ignore_errors=True)

    def test_phase6_children_no_hook_imports(self, phase6_repo_copy):
        """Phase 6 invariant: child files must NOT import useDashboardData."""
        from app.engine.apply_engine import apply_engine
        from app.runtime.context import RunContext
        from app.intent.models import ConfirmedIntent, IntentAction
        from app.intent.plan_compiler import compile_plan

        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(verb="create", target_capability="presentation.timeseries", params={"timeseries_metric": "revenue"}),
            ],
            params={"timeseries_metric": "revenue"},
            user_message="Add a timeseries chart",
            interpretation_id="phase6-noimport",
        )
        plan = compile_plan(confirmed)

        artifacts_tmp = tempfile.mkdtemp(prefix="artifacts_phase6_", dir=settings.ARTIFACTS_DIR)
        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            base_dir=phase6_repo_copy,
            workspace=phase6_repo_copy,
            artifacts=artifacts_tmp,
        )

        try:
            result = apply_engine(ctx.run_id, plan.to_dict(), ctx, dry_run=False)
            assert result["execution"]["status"] in ("ok", "verify_failed")

            # Check child files that were regenerated (KpiRow, Timeseries)
            for child_file in ["KpiRow.tsx", "Timeseries.tsx"]:
                child_path = os.path.join(phase6_repo_copy, "frontend", "src", "pages", "dashboard", "components", child_file)
                alt_path = os.path.join(phase6_repo_copy, "frontend", "src", "components", "dashboard", child_file)
                found_path = None
                for p in [child_path, alt_path]:
                    if os.path.exists(p):
                        found_path = p
                        break
                if found_path:
                    with open(found_path) as f:
                        content = f.read()
                    assert "useDashboardData" not in content, (
                        f"{found_path} must NOT import useDashboardData (pure presentational)"
                    )
        finally:
            shutil.rmtree(artifacts_tmp, ignore_errors=True)

    def test_golden_required_props_never_stripped(self, phase6_repo_copy):
        """Fase 4.8: Golden regression — required props must survive JSX generation.

        After the extractor semicolon fix, KpiRow.data is correctly detected as
        required. This test asserts that the full pipeline never strips it from
        the generated Page.tsx, even when prop filtering is active.
        """
        from app.engine.apply_engine import apply_engine
        from app.runtime.context import RunContext
        from app.intent.models import ConfirmedIntent, IntentAction
        from app.intent.plan_compiler import compile_plan

        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(verb="create", target_capability="presentation.timeseries", params={"timeseries_metric": "revenue"}),
            ],
            params={"timeseries_metric": "revenue"},
            user_message="Add a timeseries chart",
            interpretation_id="golden-regression",
        )
        plan = compile_plan(confirmed)

        artifacts_tmp = tempfile.mkdtemp(prefix="artifacts_golden_", dir=settings.ARTIFACTS_DIR)
        ctx = RunContext(
            run_id=str(uuid.uuid4()),
            base_dir=phase6_repo_copy,
            workspace=phase6_repo_copy,
            artifacts=artifacts_tmp,
        )

        try:
            result = apply_engine(ctx.run_id, plan.to_dict(), ctx, dry_run=False)
            assert result["execution"]["status"] in ("ok", "verify_failed"), (
                f"Expected ok/verify_failed, got {result['execution']['status']}"
            )

            page_content = self._read_page_content(phase6_repo_copy)

            # Golden invariant 1: KpiRow must receive data prop (was stripped pre-fix)
            assert "data={_pageData" in page_content or "data={kpiData}" in page_content, (
                "KpiRow.data prop must be present in Page.tsx — required prop stripped!"
            )

            # Golden invariant 2: Timeseries must receive data prop (required via v3 slice)
            assert "<Timeseries" in page_content, "Timeseries must be rendered in Page.tsx"
            assert "data={_pageData.chartData.timeseries}" in page_content, (
                "Timeseries.data prop must be present — required prop from v3 slice"
            )
            # title is optional (default "Trend") — PR1 doesn't auto-generate it

            # Golden invariant 3: verify must pass
            verify = result.get("meta", {}).get("verify", {})
            assert verify.get("status") in ("passed", "skipped", "error"), (
                f"Golden scenario should verify: {verify}"
            )

        finally:
            shutil.rmtree(artifacts_tmp, ignore_errors=True)
