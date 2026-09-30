"""S2·Fase3 — Matriz de aceptación del contrato target-instancia (A1+A4).

WHAT/WHERE por acción: la capability del contexto destino se expresa como
attach.target.capability y la instancia física como attach.target.instance_label.
El Confirmed Plan es la ÚNICA autoridad semántica; AnchorResolver solo resuelve
el destino físico (responsabilidad A4). Casos de la matriz (diseño Fase 2):

  A  attach explícito + 1 instancia        → MODIFY exacta, ok
  B  standalone (sin attach, sin composición) → crear standalone, sin MODIFY
  C  attach a instancia inexistente (0)    → target_not_found (conflicto)
  D  attach con label ambiguo (N)          → target_ambiguity (conflicto)
  E  singleton físico con target frozen    → resuelve exacto vía attach
  F  composición misma-contract sin attach → regresión: branch contract intacto
  G  G1 closed-world NO evalúa attach.target.capability (destino físico)
  N + explicit                             → forced override del attach

Cada test ejercita apply_engine() (GraphIR → renderer → Anchor Resolution →
git) contra una copia del repo real agent-test-repo.
"""

import os
import shutil
import subprocess
import tempfile
import uuid

import pytest

import tests.e2e.test_c4_anchor_physical_matrix as c4

AGENT_TEST_REPO = c4.AGENT_TEST_REPO
PAGE_REL = c4.PAGE_REL


@pytest.fixture
def repo_copy():
    from app.config.settings import settings

    if not os.path.isdir(AGENT_TEST_REPO):
        pytest.skip("agent-test-repo not available at " + AGENT_TEST_REPO)
    tmpdir = tempfile.mkdtemp(prefix="f3_matrix_", dir=settings.RUNS_DIR)
    shutil.copytree(
        os.path.join(AGENT_TEST_REPO, "frontend"),
        os.path.join(tmpdir, "frontend"),
        symlinks=True,
        ignore_dangling_symlinks=True,
    )
    opencode_dir = os.path.join(tmpdir, ".opencode")
    os.makedirs(opencode_dir, exist_ok=True)
    shutil.copy2(
        os.path.join(os.path.dirname(__file__), "..", "..", "backend", "config", "data_access.json"),
        os.path.join(opencode_dir, "data_access.json"),
    )
    subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


def _apply(ws, attach_label=None, forced_anchor_path=None):
    from app.engine.apply_engine import apply_engine
    from app.runtime.context import RunContext
    from app.config.settings import settings

    action = c4._filter_panel_plan(attach_label=attach_label)
    artifacts_tmp = tempfile.mkdtemp(prefix="f3_artifact_", dir=settings.ARTIFACTS_DIR)
    ctx = RunContext(
        run_id=str(uuid.uuid4()),
        base_dir=ws,
        workspace=ws,
        artifacts=artifacts_tmp,
    )
    try:
        return apply_engine(
            ctx.run_id, action, ctx,
            dry_run=True, forced_anchor_path=forced_anchor_path,
        )
    finally:
        shutil.rmtree(artifacts_tmp, ignore_errors=True)


def _audit(result, component="FilterPanel") -> dict:
    audit = result.get("meta", {}).get("audit", {}).get("anchor_resolution", {})
    assert component in audit, f"missing audit record: {audit}"
    return audit[component]


class TestCaseA_AttachExplicitSingle:
    def test_attach_exact_instance_is_mounted(self, repo_copy):
        result = _apply(repo_copy, attach_label="SalesOverviewPage")
        ex = result["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        ops = ex["operations"]
        assert any(op["action"] == "modify" and PAGE_REL in op["path"] for op in ops)
        anchor = next(o for o in ops if o["action"] == "modify" and PAGE_REL in o["path"])
        assert "FilterPanel" in anchor.get("content", "")
        rec = _audit(result)
        assert rec["decision"] == "single"
        assert rec["basis"] == "attach_target"
        assert rec["authority"] == "plan"


class TestCaseB_StandaloneOrphan:
    def test_orphan_without_attach_is_standalone(self, repo_copy):
        result = _apply(repo_copy)
        ex = result["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        assert ex.get("conflict") is None, ex
        ops = ex["operations"]
        assert any(op["action"] == "create" and "FilterPanel" in op["path"] for op in ops)
        assert not any(op["action"] == "modify" and "pages/" in op["path"] for op in ops), (
            f"standalone: no debe haber MODIFY de páginas: {ops}"
        )
        rec = _audit(result)
        assert rec["decision"] == "standalone"
        assert rec["basis"] == "standalone_orphan"
        assert rec["authority"] == "physical"


class TestCaseC_TargetNotFound:
    def test_attach_to_missing_instance_conflicts(self, repo_copy):
        ws = shutil.copytree(repo_copy, f"{repo_copy}c", dirs_exist_ok=True)
        os.remove(os.path.join(ws, PAGE_REL))
        try:
            result = _apply(ws, attach_label="SalesOverviewPage")
            ex = result["execution"]
            assert ex["status"] == "clarification_needed", ex
            assert ex.get("conflict") == "target_not_found", ex
            assert ex.get("operations") == [], ex
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_attach_to_missing_instance_with_forced_path_ok(self, repo_copy):
        """forced override: incluso con attach a instancia inexistente, el
        forced path del plan es explícito y gana (Caso 'N + explicit')."""
        forced = os.path.join(repo_copy, PAGE_REL)
        result = _apply(repo_copy, attach_label="Nope", forced_anchor_path=forced)
        ex = result["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        assert any(op["action"] == "modify" and PAGE_REL in op["path"] for op in ex["operations"])
        rec = _audit(result)
        assert rec["decision"] == "forced"
        assert rec["basis"] == "forced"
        assert rec["authority"] == "plan"


class TestCaseD_AmbiguousTarget:
    def test_attach_ambiguous_label_conflicts(self, repo_copy):
        ws = shutil.copytree(repo_copy, f"{repo_copy}d", dirs_exist_ok=True)
        homonym = os.path.join("frontend", "src", "pages", "analytics", "SalesOverviewPage.tsx")
        os.makedirs(os.path.dirname(os.path.join(ws, homonym)), exist_ok=True)
        with open(os.path.join(ws, homonym), "w") as f:
            f.write(c4.SECOND_PAGE)
        try:
            result = _apply(ws, attach_label="SalesOverviewPage")
            ex = result["execution"]
            assert ex["status"] == "clarification_needed", ex
            assert ex.get("conflict") == "target_ambiguity", ex
            assert ex.get("operations") == [], ex
        finally:
            shutil.rmtree(ws, ignore_errors=True)


class TestCaseE_SingletonFrozen:
    def test_label_normalization_matches_name(self, repo_copy):
        """La gramática de instance_label es el stem normalizado: 'Sales
        OverviewPage' encuentra SalesOverviewPage.tsx."""
        result = _apply(repo_copy, attach_label="Sales Overview Page")
        ex = result["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        assert any(op["action"] == "modify" and PAGE_REL in op["path"] for op in ex["operations"])


class TestCaseG_G1ClosedWorldExemption:
    def test_attach_target_capability_is_not_an_action_target(self, repo_copy):
        """G1 (closed-world) evalúa SOLO el target_capability de cada acción
        del plan (el hijo). El attach.target.capability (layout.page) NO está
        en analytics.filter y NO debe generar missing_component: es un destino
        físico validado por Anchor Resolution, no una capability a ejecutar."""
        result = _apply(repo_copy, attach_label="SalesOverviewPage")
        ex = result["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        assert ex.get("conflict") is None, ex
        assert ex.get("conflict") != "missing_component", ex


class TestCaseF_CompositionRegression:
    def test_contract_composition_without_attach_still_intact(self, repo_copy):
        """Misma-contract composition (dashboard.sales_overview, kpi→page) sin
        attach: el branch contract_composition se conserva (regresión)."""
        from app.engine.apply_engine import apply_engine
        from app.runtime.context import RunContext
        from app.intent.models import ConfirmedIntent, IntentAction
        from app.intent.plan_compiler import compile_plan
        from app.config.settings import settings

        ws = shutil.copytree(repo_copy, f"{repo_copy}f", dirs_exist_ok=True)
        # KpiRow NO existe → CREATE real de un composición-child del contrato.
        for rel in (
            "frontend/src/components/dashboard/KpiRow.tsx",
            "frontend/src/components/dashboard/KpiRow.module.css",
        ):
            p = os.path.join(ws, rel)
            if os.path.exists(p):
                os.remove(p)
        try:
            confirmed = ConfirmedIntent(
                contract_id="dashboard.sales_overview",
                contract_version=1,
                actions=[
                    IntentAction(
                        verb="create", target_capability="presentation.kpi_row",
                        params={"metrics": ["revenue"]},
                    ),
                ],
                params={"metrics": ["revenue"]},
                user_message="Add a KPI row",
                interpretation_id="f3-composition",
            )
            plan = compile_plan(confirmed).to_dict()
            artifacts_tmp = tempfile.mkdtemp(prefix="f3_comp_", dir=settings.ARTIFACTS_DIR)
            ctx = RunContext(
                run_id=str(uuid.uuid4()), base_dir=ws,
                workspace=ws, artifacts=artifacts_tmp,
            )
            try:
                result = apply_engine(ctx.run_id, plan, ctx, dry_run=True)
                ex = result["execution"]
                assert ex["status"] in ("ok", "verify_failed"), ex
                assert ex.get("conflict") is None, ex
                assert any(op["action"] == "modify" and PAGE_REL in op["path"]
                           for op in ex["operations"])
            finally:
                shutil.rmtree(artifacts_tmp, ignore_errors=True)
        finally:
            shutil.rmtree(ws, ignore_errors=True)