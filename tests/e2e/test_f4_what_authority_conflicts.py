"""S2·F4 E2E — el Confirmed Plan es la única autoridad del WHAT.

apply_engine() contra el repositorio real: el target_capability confirmado
gobierna qué capability se materializa, y su ausencia o invalidez producen
CONFLICT tipificado (nunca matching, nunca fallback, nunca lifecycle inyectado).

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

from app.config.settings import settings

AGENT_TEST_REPO = "/opt/agent-repos/agent-test-repo"

CAP = "presentation.kpi_row"


@pytest.fixture(scope="module")
def repo_copy():
    """Temp copy del agent-test-repo con git init (bajo settings.RUNS_DIR)."""
    if not os.path.isdir(AGENT_TEST_REPO):
        pytest.skip("agent-test-repo not available at " + AGENT_TEST_REPO)

    tmpdir = tempfile.mkdtemp(prefix="f4_what_", dir=settings.RUNS_DIR)
    src = os.path.join(AGENT_TEST_REPO, "frontend")
    dst = os.path.join(tmpdir, "frontend")
    shutil.copytree(src, dst, symlinks=True, ignore_dangling_symlinks=True)
    subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmpdir, capture_output=True)
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


def _plan(actions: list[dict], intents: list[dict] | None = None,
          contract_id: str = "dashboard.sales_overview") -> dict:
    return {
        "contract_id": contract_id,
        "skill_ir": {
            "contract_id": contract_id, "version": 1,
            "params": {"metrics": ["revenue"]}, "confidence": 1.0,
        },
        "semantic_frame": {
            "actions": [dict(a) for a in actions],
            "objects": [], "constraints": [], "confidence": 1.0, "missing_info": [],
        },
        "actions": [dict(a) for a in actions],
        "intents": intents or [{"id": "i1", "capability": CAP}],
    }


def _apply(workspace: str, plan: dict, dry_run: bool = True) -> dict:
    from app.engine.apply_engine import apply_engine
    from app.runtime.context import RunContext

    artifacts = tempfile.mkdtemp(prefix="f4_art_", dir=settings.ARTIFACTS_DIR)
    try:
        ctx = RunContext(run_id=str(uuid.uuid4()), base_dir=workspace,
                         workspace=workspace, artifacts=artifacts)
        return apply_engine(ctx.run_id, plan, ctx, dry_run=dry_run)
    finally:
        shutil.rmtree(artifacts, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
# D / invariante: target ausente o inválido → CONFLICT tipificado
# ═══════════════════════════════════════════════════════════════════


class TestConfirmedPlanWithoutAuthority:
    def test_missing_target_capability_is_invalid_confirmed_plan(self, repo_copy):
        plan = _plan([{"verb": "create", "object": "kpi"}])
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] == "clarification_needed", ex
        assert ex["conflict"] == "invalid_confirmed_plan", ex
        assert ex["operations"] == [], ex

    def test_blank_target_capability_is_invalid_confirmed_plan(self, repo_copy):
        plan = _plan([{"verb": "create", "object": "kpi",
                       "target_capability": "  "}])
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["conflict"] == "invalid_confirmed_plan", ex
        assert ex["operations"] == [], ex

    def test_unknown_target_capability_is_missing_component(self, repo_copy):
        """WHAT no expresable → conflicto. Nunca otra capability parecida."""
        plan = _plan(
            [{"verb": "create", "object": "chart",
              "target_capability": "capability.inexistente"}],
            intents=[{"id": "i1", "capability": "capability.inexistente"}],
        )
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] == "clarification_needed", ex
        assert ex["conflict"] == "missing_component", ex
        assert ex["operations"] == [], ex

    def test_out_of_contract_target_never_materializes(self, repo_copy):
        """target fuera del contrato NO entra en el scope del IR."""
        plan = _plan(
            [{"verb": "create", "object": "filter",
              "target_capability": "presentation.filter_panel"}],
            intents=[{"id": "i1", "capability": "presentation.filter_panel"}],
        )
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] == "clarification_needed", ex
        assert ex["conflict"] == "missing_component", ex
        assert ex["operations"] == [], ex

    def test_absent_plan_actions_is_invalid_confirmed_plan(self, repo_copy):
        """El frame declara acciones, pero plan['actions'] no → CONFLICT."""
        plan = _plan([{"verb": "create", "object": "kpi",
                       "target_capability": CAP}])
        plan.pop("actions")
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] == "clarification_needed", ex
        assert ex["conflict"] == "invalid_confirmed_plan", ex
        assert ex["operations"] == [], ex

    def test_plan_actions_shorter_than_frame_is_invalid_confirmed_plan(self, repo_copy):
        """plan['actions'] debe cubrir 1:1 las acciones funcionales del frame."""
        plan = _plan([{"verb": "create", "object": "kpi",
                       "target_capability": CAP}])
        plan["actions"] = []
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["conflict"] == "invalid_confirmed_plan", ex
        assert ex["operations"] == [], ex


# ═══════════════════════════════════════════════════════════════════
# B / C — el target explícito gana frente a matching e inferencia
# ═══════════════════════════════════════════════════════════════════


class TestExplicitTargetWins:
    def test_object_chart_does_not_touch_chart_capability(self, repo_copy):
        """object 'chart' (legacy → chart.*) con target kpi_row → solo kpi_row."""
        plan = _plan([{"verb": "modify", "object": "chart",
                       "target_capability": CAP}])
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        touched = [op for op in ex["operations"] if op["action"] == "modify"]
        assert touched, ex
        assert any("KpiRow" in op["path"] for op in touched), ex
        assert not any("Chart" in op["path"] for op in touched), ex

    def test_object_metric_does_not_flip_to_other_metric_capability(self, repo_copy):
        plan = _plan([{"verb": "modify", "object": "metric",
                       "target_capability": CAP}])
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        touched = [op for op in ex["operations"] if op["action"] == "modify"]
        assert touched, ex
        assert all("KpiRow" in op["path"] for op in touched), ex

    def test_unconfirmed_capability_gets_no_operation(self, repo_copy):
        """Solo la capability confirmada genera operación."""
        plan = _plan([{"verb": "modify", "object": "kpi",
                       "target_capability": CAP}])
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        touched = {op["path"] for op in ex["operations"]}
        assert touched, ex
        assert all("KpiRow" in p for p in touched), ex

    def test_metrics_params_do_not_inject_lifecycle(self, repo_copy):
        """params con 'metrics' NO crean MODIFY kpi_row si no está confirmada.

        Antes de S2·F4 el post-pass A hacía exactamente esto.
        """
        plan = _plan([{"verb": "remove", "object": "page",
                       "target_capability": "layout.page"}])
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        assert not any("KpiRow" in op["path"] and op["action"] == "modify"
                       for op in ex["operations"]), ex


# ═══════════════════════════════════════════════════════════════════
# J — request obsoleto no puede cambiar el plan confirmado (D2)
# ═══════════════════════════════════════════════════════════════════


class TestStaleRequestCannotOverridePlan:
    def test_confirmed_plan_is_what_gets_materialized(self, repo_copy):
        confirmed = _plan([{"verb": "modify", "object": "kpi",
                            "target_capability": CAP}])
        # El intent del cliente pide otra cosa; el plan confirmado manda.
        confirmed["intents"] = [{"id": "i1", "capability": "layout.page"}]
        ex = _apply(repo_copy, confirmed)["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        assert any("KpiRow" in op["path"] for op in ex["operations"]), ex
        assert not any("Page" in op["path"] for op in ex["operations"]), ex