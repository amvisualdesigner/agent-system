"""S2·F3·F1 — G1: no silent-drop.

Una acción confirmada (create/modify/remove/...) cuyo target capability NO es
expresable en el contrato seleccionado (closed-world) NUNCA termina como
status=ok con operations=[] ni se cae silenciosamente de planes mixtos.

Casos obligatorios (contrato Fase 1·G1):
  Caso 1  dashboard.sales_overview + filter_panel  → NI ok NI ops=[], sin write,
          sin commit, sin reinterpretación (conflicto explícito).
  Caso 2  capability válida del mismo contrato     → comportamiento actual
          (sin regresión).
  Caso 3  capability desconocida                   → comportamiento explícito,
          nunca silent-drop.
  Caso 4  RepositoryValidation es posterior        → el drop se detecta en la
          capa estructural (missing_component), NO por RepositoryValidation.
"""

import os
import shutil
import subprocess
import tempfile
import uuid

import pytest

from app.config.settings import settings
from app.intent.models import ConfirmedIntent, IntentAction
from app.intent.plan_compiler import compile_plan

AGENT_TEST_REPO = "/opt/agent-repos/agent-test-repo"

FILTER_PANEL_REL = os.path.join("frontend", "src", "components", "FilterPanel.tsx")


@pytest.fixture
def seeded_workspace():
    """Copy of agent-test-repo (like phase6 fixtures) inside RUNS_DIR."""
    if not os.path.isdir(AGENT_TEST_REPO):
        pytest.skip("agent-test-repo not available at " + AGENT_TEST_REPO)

    tmpdir = tempfile.mkdtemp(prefix="g1_safety_", dir=settings.RUNS_DIR)
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


def _plan(contract_id: str, verb: str, target: str) -> dict:
    confirmed = ConfirmedIntent(
        contract_id=contract_id,
        contract_version=1,
        actions=[IntentAction(verb=verb, target_capability=target, params={})],
        params={},
        user_message="test",
        interpretation_id="g1-safety",
    )
    return compile_plan(confirmed).to_dict()


def _apply(workspace: str, plan: dict) -> dict:
    from app.engine.apply_engine import apply_engine
    from app.runtime.context import RunContext

    artifacts_tmp = tempfile.mkdtemp(prefix="g1_artifact_", dir=settings.ARTIFACTS_DIR)
    ctx = RunContext(
        run_id=str(uuid.uuid4()),
        base_dir=workspace,
        workspace=workspace,
        artifacts=artifacts_tmp,
    )
    try:
        return apply_engine(ctx.run_id, plan, ctx, dry_run=True)
    finally:
        shutil.rmtree(artifacts_tmp, ignore_errors=True)


class TestG1NoSilentDrop:
    """Caso 1 — dashboard.sales_overview + presentation.filter_panel."""

    def test_caso1_no_silent_drop(self, seeded_workspace):
        ws = seeded_workspace
        plan = _plan("dashboard.sales_overview", "create", "presentation.filter_panel")
        result = _apply(ws, plan)

        ex = result["execution"]

        # NI status=ok NI operations=[] como éxito silencioso.
        assert ex["status"] != "ok", ex
        assert ex.get("operations") in ([], None), ex
        # Conflicto explícito con la semántica existente.
        assert ex["status"] == "conflict", ex
        assert ex.get("conflict") == "missing_component", ex
        assert "cannot be expressed" in ex.get("detail", "") or "not in contract" in ex.get("detail", ""), ex
        # Sin reinterpretación: ninguna operation CREATE/MODIFY emitida.
        assert ex.get("operations") == [], ex

        # Sin escritura: FilterPanel NO debe materializarse.
        assert not os.path.exists(os.path.join(ws, FILTER_PANEL_REL)), (
            "drop silencioso: FilterPanel no debe crearse"
        )

        # Sin commit: git log debe seguir solo con el commit inicial (o vacío).
        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=ws, capture_output=True, text=True,
        ).stdout.strip()
        assert "FILTER" not in log.upper(), log

    def test_caso1_mixed_plan_never_partially_applies(self, seeded_workspace):
        """Planes mixtos: kpi_row (válido) + filter_panel (no expresable) →
        NUNCA se aplica parcialmente ni se silencia el filter_panel."""
        ws = seeded_workspace
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                IntentAction(verb="create", target_capability="presentation.kpi_row", params={}),
                IntentAction(verb="create", target_capability="presentation.filter_panel", params={}),
            ],
            params={},
            user_message="test",
            interpretation_id="g1-safety",
        )
        plan = compile_plan(confirmed).to_dict()
        result = _apply(ws, plan)
        ex = result["execution"]

        assert ex["status"] == "conflict", ex
        assert ex.get("conflict") == "missing_component", ex
        assert ex.get("operations") == [], ex

    def test_caso2_valid_capability_no_regression(self, seeded_workspace):
        """Caso 2 — capability válida del mismo contrato → flujo actual intacto."""
        ws = seeded_workspace
        plan = _plan("dashboard.sales_overview", "create", "presentation.kpi_row")
        result = _apply(ws, plan)
        ex = result["execution"]

        # Comportamiento preexistente: CREATE sobre un target ya existente → conflicto
        # explícito (nunca ok silencioso); CREATE sobre target ausente → materializa.
        assert ex["status"] in ("conflict", "ok", "verify_failed"), ex
        assert ex.get("conflict") != "missing_component", ex
        ops = ex.get("operations") or []
        if ex["status"] in ("ok", "verify_failed"):
            assert any(o["action"] == "create" for o in ops), ops

    def test_caso3_unknown_capability_no_silent_drop(self, seeded_workspace):
        """Caso 3 — capability desconocida → comportamiento explícito, nunca
        status=ok con operations=[]."""
        ws = seeded_workspace
        plan = _plan("dashboard.sales_overview", "create", "no.such.capability")
        result = _apply(ws, plan)
        ex = result["execution"]

        assert ex["status"] == "conflict", ex
        assert ex.get("conflict") == "missing_component", ex
        assert ex.get("operations") == [], ex

    def test_caso4_detected_at_structural_layer(self, seeded_workspace):
        """Caso 4 — la pérdida se detecta en la capa estructural (missing_component),
        NO es responsabilidad de RepositoryValidation (repository_conflict)."""
        ws = seeded_workspace
        plan = _plan("dashboard.sales_overview", "create", "presentation.filter_panel")
        result = _apply(ws, plan)
        ex = result["execution"]

        assert ex["status"] == "conflict", ex
        assert ex.get("conflict") == "missing_component", ex
        assert ex.get("conflict") != "repository_conflict", (
            "RepositoryValidation NO debe ser quien corrija la pérdida semántica"
        )
        assert ex.get("operations") == [], ex