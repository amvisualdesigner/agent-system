"""Fase 6.2 — E2E: el source del binding confirmado atraviesa al renderer.

Escenario real (dashboard.sales_overview): un workspace que ya consume
`useSalesData` (hook NO registry) para KpiRow identifica la fuente como
`auto_unique`. El create de `presentation.kpi_row` confirma el binding
`useSalesData` y el apply:

  * baja el source confirmado a `DataSourceIR(hook_name=useSalesData,
    hook_import=@/hooks/useSalesData)` con el probe de import determinista,
  * el FEAT Import/declaracion del renderer preferen `hook_name`/`hook_import`
    (el registry `useDashboardData` es evidencia de drift y ya no sustituye),
  * el compiler indexa props por node.type: la capability del Plan se retargetea
    a `KpiRow` para que el binding NO quede inerte en el render.
  * host In re-apply tras BORRAR el hook => CONFLICT retryable
    (source_lowering_conflict / missing_hook), sin fallback al registry.
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
  return <KpiRow metrics={metrics} />;
}
"""

_HOOK = "export function useSalesData() { return { metrics: [] as any[] }; }\n"


def _seed(workspace: str) -> None:
    os.makedirs(os.path.join(workspace, "frontend", "src", "pages", "dashboard"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "frontend", "src", "hooks"), exist_ok=True)
    with open(os.path.join(workspace, "frontend/src/pages/dashboard/SalesOverviewPage.tsx"), "w") as f:
        f.write(_PAGE)
    with open(os.path.join(workspace, "frontend/src/hooks/useSalesData.ts"), "w") as f:
        f.write(_HOOK)


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
                {"verb": "create", "target_capability": "presentation.kpi_row"},
                {"verb": "modify", "target_capability": "layout.page"},
            ],
            params={"metrics": ["revenue"], "timeseries_metric": "revenue"},
            user_message="test",
        ))
    finally:
        delete_run_state(run_id)


def _apply(workspace: str, artifacts: str, plan: dict) -> dict:
    ctx = RunContext(
        run_id=str(uuid.uuid4()),
        workspace=workspace,
        base_dir=workspace,
        artifacts=artifacts,
    )
    plan = {**plan, "gate": {"blocked": False}}
    snapshot = json.loads(json.dumps(plan))
    result = apply_engine(ctx.run_id, plan, ctx, dry_run=True)
    return result, snapshot


class TestSourceLoweringEndToEnd:
    def test_auto_unique_confirm_carries_use_sales_data(self, e2e_workspace):
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
        assert mappings.get("metrics") == "_pageData.metrics"

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
        assert "metrics={_pageData.metrics}" in page

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