"""Fase 6.2 — E2E: el source del binding confirmado atraviesa al renderer.

Escenario real (dashboard.sales_overview): un workspace que ya consume
`useSalesData` (hook NO registry) para KpiRow. El slot data←metrics hace que
el create de `presentation.kpi_row` proponga `needs_choice`: el registry
tambien declara `data <- contract_params['metrics']`. El humano confirma la
candidata probada `useSalesData` (data→_pageData.metrics, seleccion humana
que NO rediscoverya) y el apply:

  * baja el source confirmado a `DataSourceIR(hook_name=useSalesData,
    hook_import=@/hooks/useSalesData)` con el probe de import determinista,
  * el FEAT Import/declaracion del renderer preferen `hook_name`/`hook_import`
    (el registry `useDashboardData` es evidencia de drift y ya no sustituye),
  * el compiler indexa props por node.type: la capability del Plan se retargetea
    a `KpiRow` para que el binding NO quede inerte en el render.
  * host In re-apply tras BORRAR el hook => CONFLICT retryable
    (source_lowering_conflict / missing_hook), sin fallback al registry.

FASE 6.3 (decisiones D2-A/D3 ratificadas):

  * E2E-B: quitar el campo CONFIRMADO desestructurado de la pagina entre
    confirm y apply => CONFLICT retryable (missing_field), sin FileOps.
  * E2E-C: restaurar el campo y RETRY con el MISMO Plan => ok, misma fuente,
    sin rediscovery.
  * E2E-D: registry resuelve un VALOR CONCRETO (SearchBar.placeholder default)
    => drift estructurado `registry_concrete_value` (preview de forma, nunca
    el valor), confirmed gana, apply NO bloquea.
"""

import json
import os
import subprocess
import uuid

from app.binding.draft_proposal import enrich_draft_binding_proposals
from app.engine.apply_engine import apply_engine
from app.intent.models import RunPhase
from app.runtime.context import RunContext
from app.state.run_state import delete_run_state, save_run_state


_PAGE = """\
import { KpiRow } from './KpiRow';
import { useSalesData } from '@/hooks/useSalesData';
export function SalesOverviewPage() {
  const { metrics } = useSalesData();
  return <KpiRow data={metrics} />;
}
"""

_HOOK = "export function useSalesData() { return { metrics: [] as any[] }; }\n"

_SEARCH_PAGE = """\
import { SearchBar } from '@/components/SearchBar';
import { useSearchData } from '@/hooks/useSearchData';
export function SearchPage() {
  const { placeholder } = useSearchData();
  return <SearchBar placeholder={placeholder} />;
}
"""

_SEARCH_HOOK = "export function useSearchData() { return { placeholder: 'Type...' }; }\n"


def _seed(workspace: str) -> None:
    os.makedirs(os.path.join(workspace, "frontend", "src", "pages", "dashboard"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "frontend", "src", "hooks"), exist_ok=True)
    with open(os.path.join(workspace, "frontend/src/pages/dashboard/SalesOverviewPage.tsx"), "w") as f:
        f.write(_PAGE)
    with open(os.path.join(workspace, "frontend/src/hooks/useSalesData.ts"), "w") as f:
        f.write(_HOOK)


def _seed_search(workspace: str) -> None:
    os.makedirs(os.path.join(workspace, "frontend", "src", "pages", "search"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "frontend", "src", "hooks"), exist_ok=True)
    with open(os.path.join(workspace, "frontend/src/pages/search/SearchPage.tsx"), "w") as f:
        f.write(_SEARCH_PAGE)
    with open(os.path.join(workspace, "frontend/src/hooks/useSearchData.ts"), "w") as f:
        f.write(_SEARCH_HOOK)


def _commit(workspace: str, msg: str = "seed"):
    subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg, "--allow-empty"], cwd=workspace, capture_output=True)


def _confirm(run_id: str, workspace: str) -> dict:
    from app.api.agent_confirm import ConfirmRequest, _agent_confirm

    draft = {
        "status": "ok",
        "contract_id": "dashboard.sales_overview",
        "contract_version": 1,
        "interpretation_id": f"i-{run_id}",
        "proposed_actions": [
            {"verb": "create", "target_capability": "presentation.kpi_row",
             "contract_id": "dashboard.sales_overview"},
            {"verb": "modify", "target_capability": "layout.page",
             "contract_id": "dashboard.sales_overview"},
        ],
        "alternatives": [],
        "params_proposed": {"metrics": ["revenue"], "timeseries_metric": "revenue"},
        "worktree_capabilities": [],
    }
    enrich_draft_binding_proposals(draft, workspace=workspace)
    save_run_state(run_id, {
        "phase": RunPhase.AWAITING_CONFIRMATION.value,
        "interpretation_draft": draft,
    })
    try:
        return _agent_confirm(ConfirmRequest(
            run_id=run_id,
            interpretation_id=f"i-{run_id}",
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[
                {"verb": "create", "target_capability": "presentation.kpi_row",
                 "binding": {
                     "source": {"kind": "hook", "ref": "useSalesData"},
                     "mapping": [{"prop": "data", "from_field": "_pageData.metrics"}],
                 }},
                {"verb": "modify", "target_capability": "layout.page"},
            ],
            params={"metrics": ["revenue"], "timeseries_metric": "revenue"},
            user_message="test",
        ))
    finally:
        delete_run_state(run_id)


def _apply(workspace: str, artifacts: str, plan: dict) -> tuple:
    from app.engine.apply_engine import apply_engine
    from app.runtime.context import RunContext

    run_id = str(uuid.uuid4())
    ctx = RunContext(
        run_id=run_id,
        base_dir=workspace,
        workspace=workspace,
        artifacts=artifacts,
    )
    result = apply_engine(run_id, plan, ctx, dry_run=True)
    return result, ctx


def _confirm_search(run_id: str, workspace: str) -> dict:
    """Confirma un create de interaction.search con seleccion humana explicita."""
    from app.api.agent_confirm import ConfirmRequest, _agent_confirm

    draft = {
        "status": "ok",
        "contract_id": "interaction.search",
        "contract_version": 1,
        "interpretation_id": f"i-{run_id}",
        "proposed_actions": [
            {"verb": "create", "target_capability": "interaction.search",
             "contract_id": "interaction.search"},
        ],
        "alternatives": [],
        "params_proposed": {"placeholder": "Foo Bar"},
        "worktree_capabilities": [],
    }
    enrich_draft_binding_proposals(draft, workspace=workspace)
    save_run_state(run_id, {
        "phase": RunPhase.AWAITING_CONFIRMATION.value,
        "interpretation_draft": draft,
    })
    try:
        return _agent_confirm(ConfirmRequest(
            run_id=run_id,
            interpretation_id=f"i-{run_id}",
            contract_id="interaction.search",
            contract_version=1,
            actions=[{
                "verb": "create", "target_capability": "interaction.search",
                "binding": {
                    "source": {"kind": "hook", "ref": "useSearchData"},
                    "mapping": [{"prop": "placeholder", "from_field": "_pageData.placeholder"}],
                },
            }],
            params={"placeholder": "Foo Bar"},
            user_message="test",
        ))
    finally:
        delete_run_state(run_id)


class TestSourceLoweringEndToEnd:
    def test_human_choice_confirm_carries_use_sales_data(self, e2e_workspace):
        _seed(e2e_workspace)
        out = _confirm(str(uuid.uuid4()), e2e_workspace)
        assert out["status"] == "ok"
        plan = out["plan"]
        kpi_actions = [a for a in plan["actions"] if a["target_capability"] == "presentation.kpi_row"]
        assert len(kpi_actions) == 1
        binding = kpi_actions[0]["binding"]
        assert binding["source"]["kind"] == "hook"
        assert binding["source"]["ref"] == "useSalesData"
        mappings = {m["prop"]: m["from_field"] for m in binding["mapping"]}
        assert mappings.get("data") == "_pageData.metrics"

    def test_apply_renders_confirmed_source_not_registry(
        self, e2e_workspace, artifacts_dir,
    ):
        _seed(e2e_workspace)
        _commit(e2e_workspace)
        out = _confirm(str(uuid.uuid4()), e2e_workspace)
        assert out["status"] == "ok"

        result, _ = _apply(e2e_workspace, artifacts_dir, out["plan"])
        assert result["execution"]["status"] == "ok"

        ops = result["execution"]["operations"]
        page_ops = [op for op in ops if op["action"] == "modify" and op["path"].endswith("SalesOverviewPage.tsx")]
        assert len(page_ops) == 1
        page = page_ops[0]["content"]
        assert "import { useSalesData } from '@/hooks/useSalesData'" in page
        assert "useDashboardData" not in page
        assert page.count("const _pageData = useSalesData();") == 1
        assert "data={_pageData.metrics}" in page

        create_kpi = [op for op in ops if op["action"] == "create" and op["path"].endswith("KpiRow.tsx")]
        assert len(create_kpi) == 1
        assert "metrics" in create_kpi[0]["content"]

        touched = {op["path"] for op in ops}
        assert "frontend/src/pages/dashboard/SalesOverviewPage.tsx" in touched
        assert "frontend/src/pages/dashboard/KpiRow.tsx" in touched
        assert len(touched) == 2, f"no invented FileOps, got {sorted(touched)}"

        # el Plan confirmado NO se muta (sigue siendo la autoridad del WHAT)
        assert result["execution"].get("conflict") is None

    def test_apply_reports_source_drift_without_blocking(
        self, e2e_workspace, artifacts_dir,
    ):
        _seed(e2e_workspace)
        _commit(e2e_workspace)
        out = _confirm(str(uuid.uuid4()), e2e_workspace)
        assert out["status"] == "ok"

        result, _ = _apply(e2e_workspace, artifacts_dir, out["plan"])
        assert result["execution"]["status"] == "ok"
        diag = result.get("execution", {}).get("diagnostics") or result.get("diagnostics") or {}
        drift = diag.get("binding_drift", [])
        source_drift = [d for d in drift if "source" in d]
        assert any(
            d["component"] == "KpiRow"
            and d["source"] == {"confirmed": "useSalesData", "registry": "useDashboardData"}
            for d in source_drift
        ), f"expected source drift, got {drift}"

    def test_missing_hook_after_confirm_is_retryable_conflict(
        self, e2e_workspace, artifacts_dir,
    ):
        _seed(e2e_workspace)
        _commit(e2e_workspace)
        out = _confirm(str(uuid.uuid4()), e2e_workspace)
        assert out["status"] == "ok"

        os.remove(os.path.join(e2e_workspace, "frontend/src/hooks/useSalesData.ts"))
        _commit(e2e_workspace, "drop hook")

        result, _ = _apply(e2e_workspace, artifacts_dir, out["plan"])
        ex = result["execution"]
        assert ex["status"] == "conflict"
        assert ex["conflict"] == "repository_conflict"
        assert ex["plan_confirmed"] is True
        assert ex["plan_retryable"] is True
        assert ex["run_phase"] == "confirmed"
        assert ex["operations"] == []
        assert ex["diff"] is None
        details = ex.get("details") or {}
        assert details.get("cause") == "source_lowering_conflict"
        assert details.get("reason") == "missing_hook"

    def test_slice_kind_is_repository_conflict_no_registry_fallback(
        self, e2e_workspace, artifacts_dir,
    ):
        """kind=slice NUNCA cae al registry: es CONFLICT retryable en apply."""
        _seed(e2e_workspace)
        _commit(e2e_workspace)
        out = _confirm(str(uuid.uuid4()), e2e_workspace)
        assert out["status"] == "ok"
        plan = out["plan"]
        for action in plan["actions"]:
            if action.get("binding"):
                action["binding"]["source"] = {
                    "kind": "slice", "ref": "contract_params['metrics']",
                }

        result, _ = _apply(e2e_workspace, artifacts_dir, plan)
        ex = result["execution"]
        assert ex["status"] == "conflict"
        assert ex["conflict"] == "repository_conflict"
        assert ex["conflict"] != "invalid_confirmed_plan"
        assert ex["plan_confirmed"] is True
        assert ex["plan_retryable"] is True
        assert ex["run_phase"] == "confirmed"
        assert ex["operations"] == []
        assert ex["diff"] is None
        details = ex.get("details") or {}
        assert details.get("cause") == "source_lowering_conflict"
        assert details.get("reason") == "unsupported_kind"
        # El registry no escribio nada en el repo: el FileOp no toca la pagina.
        page = os.path.join(e2e_workspace, "frontend/src/pages/dashboard/SalesOverviewPage.tsx")
        assert open(page).read() == _PAGE


# ── F6.3 D2-A: campo confirmado fuera del snapshot => missing_field ─────────


class TestConfirmedFieldRootingEndToEnd:
    def test_field_removed_after_confirm_is_missing_field_conflict(
        self, e2e_workspace, artifacts_dir,
    ):
        """E2E-B: quitar `metrics` de la pagina entre confirm y apply."""
        _seed(e2e_workspace)
        _commit(e2e_workspace)
        out = _confirm(str(uuid.uuid4()), e2e_workspace)
        assert out["status"] == "ok"

        page_path = os.path.join(
            e2e_workspace, "frontend/src/pages/dashboard/SalesOverviewPage.tsx"
        )
        page = open(page_path).read()
        blocked = page.replace(
            "const { metrics } = useSalesData();", "useSalesData();",
        )
        open(page_path, "w").write(blocked)
        _commit(e2e_workspace, "drop confirmed field")

        result, _ = _apply(e2e_workspace, artifacts_dir, out["plan"])
        ex = result["execution"]
        assert ex["status"] == "conflict"
        assert ex["conflict"] == "repository_conflict"
        assert ex["plan_confirmed"] is True
        assert ex["plan_retryable"] is True
        assert ex["run_phase"] == "confirmed"
        assert ex["operations"] == []
        assert ex["diff"] is None
        details = ex.get("details") or {}
        assert details.get("cause") == "source_lowering_conflict"
        assert details.get("reason") == "missing_field"
        assert "metrics" in (details.get("detail") or "")
        # la pagina fisica no fue reescrita por el apply fallido
        assert open(page_path).read() == blocked

    def test_restore_field_and_retry_same_plan_succeeds(
        self, e2e_workspace, artifacts_dir,
    ):
        """E2E-C: restaurar el campo y RETRY con el MISMO Plan confirmado."""
        _seed(e2e_workspace)
        _commit(e2e_workspace)
        out = _confirm(str(uuid.uuid4()), e2e_workspace)
        assert out["status"] == "ok"
        plan = out["plan"]
        kpi = [a for a in plan["actions"] if a["target_capability"] == "presentation.kpi_row"][0]
        assert kpi["binding"]["source"]["ref"] == "useSalesData"

        page_path = os.path.join(
            e2e_workspace, "frontend/src/pages/dashboard/SalesOverviewPage.tsx"
        )
        page = open(page_path).read()

        # romper el campo -> conflict missing_field (sin escrituras)
        open(page_path, "w").write(page.replace(
            "const { metrics } = useSalesData();", "useSalesData();",
        ))
        _commit(e2e_workspace, "drop confirmed field")
        conflict, _ = _apply(e2e_workspace, artifacts_dir, plan)
        assert conflict["execution"]["status"] == "conflict"
        assert conflict["execution"].get("details", {}).get("reason") == "missing_field"

        # restaurar el campo y retry con el MISMO Plan: mismo binding, sin
        # rediscovery (la fuente confirmada sigue siendo useSalesData)
        open(page_path, "w").write(page)
        _commit(e2e_workspace, "restore confirmed field")
        ok, _ = _apply(e2e_workspace, artifacts_dir, plan)
        assert ok["execution"]["status"] == "ok"
        assert ok["execution"].get("conflict") is None
        page_op = [op for op in ok["execution"]["operations"]
                   if op["path"].endswith("SalesOverviewPage.tsx")]
        assert len(page_op) == 1
        assert "data={_pageData.metrics}" in page_op[0]["content"]
        assert "useDashboardData" not in page_op[0]["content"]
        assert kpi["binding"]["source"]["ref"] == "useSalesData"


# ── F6.3 D3: valor concreto del registry => drift estructurado, no bloqueo ─


class TestConcreteRegistryValueDriftEndToEnd:
    def test_concrete_registry_value_emits_structured_drift_without_blocking(
        self, e2e_workspace, artifacts_dir,
    ):
        """E2E-D: SearchBar.placeholder (default del registry) es un valor
        concreto -> DriftItem registry_concrete_value (preview de forma, nunca
        el valor); el apply NO bloquea y el Plan confirmado queda intacto."""
        _seed_search(e2e_workspace)
        _commit(e2e_workspace)
        out = _confirm_search(str(uuid.uuid4()), e2e_workspace)
        assert out["status"] == "ok"
        plan = out["plan"]
        binding = plan["actions"][0]["binding"]
        assert binding["source"] == {"kind": "hook", "ref": "useSearchData"}
        assert binding["mapping"][0]["from_field"] == "_pageData.placeholder"

        result, _ = _apply(e2e_workspace, artifacts_dir, plan)
        ex = result["execution"]
        assert ex["status"] == "ok"
        assert ex.get("conflict") is None
        assert ex["operations"], "el render no debe bloquearse por el drift"

        drift = (ex.get("diagnostics") or {}).get("binding_drift", [])
        concrete = [d for d in drift if d.get("reason") == "registry_concrete_value"]
        assert any(
            d.get("component") == "SearchBar"
            and d.get("prop") == "placeholder"
            and d.get("registry") is None
            and d.get("registry_value_shape") == "string"
            and d.get("confirmed") == "_pageData.placeholder"
            for d in concrete
        ), f"expected concrete-value drift, got {drift}"
        # el valor en si jamas se serializa como diagnostico
        assert all("Foo Bar" not in json.dumps(d) for d in drift)
        # el Plan confirmado no se muto y la fuente confirmada sigue mandando
        assert plan["actions"][0]["binding"]["source"]["ref"] == "useSearchData"