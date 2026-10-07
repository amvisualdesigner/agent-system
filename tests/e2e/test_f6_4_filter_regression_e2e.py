"""Fase 6.4 — E2E: regresión del circuito Filter (interpret → confirm → apply).

Escenario main: "Add a filter by size in sales dashboard" debe seleccionar
`analytics.filter` (NO el dashboard, aunque menciona "dashboard"), proponer
`presentation.filter_panel` con attach a `layout.page/SalesOverviewPage`,
confirmar binding `auto_unique` (useDashboardData → _pageData.filters) y
aplicar FileOps reales: CREATE components/FilterPanel.tsx + MODIFY
SalesOverviewPage.tsx (que monta el componente).

El workspace semeja F6.1 (_FILTER_PAGE, uso fisico de FilterPanel para que el
binding sea auto_unique) + un anchor destino (SalesOverviewPage) que todavia
NO importa FilterPanel: el attach del plan lo resuelve la anchor-resolver.
useDashboardData es el hook por defecto del registry ⇒ source lowering
"Caso B" (identidad) ⇒ sin missing_hook/missing_field en apply.
"""

import os
import subprocess
import uuid

from app.binding.draft_proposal import enrich_draft_binding_proposals
from app.engine.apply_engine import apply_engine
from app.intent.interpreter import _select_contract
from app.intent.models import RunPhase
from app.runtime.context import RunContext
from app.state.run_state import delete_run_state, save_run_state


# ── Seed: una pagina fisica que SI renderiza FilterPanel (fuente auto_unique)
# ── y un dashboard que todavia NO lo importa (destino del attach).

_ANALYTICS_PAGE = """\
import { FilterPanel } from './components/FilterPanel';
import { useDashboardData } from '@/hooks/useDashboardData';
export function AnalyticsPage() {
  const { filters } = useDashboardData();
  return <FilterPanel filters={filters} />;
}
"""

_SALES_PAGE = """\
import { KpiRow } from './KpiRow';
export function SalesOverviewPage() {
  const { metrics } = { metrics: [] as any[] };
  return (
    <div className="page">
      <h1>Sales Overview</h1>
      <KpiRow data={metrics} />
    </div>
  );
}
"""


def _seed(workspace: str) -> None:
    os.makedirs(os.path.join(workspace, "frontend", "src", "pages", "analytics"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "frontend", "src", "pages", "dashboard"), exist_ok=True)
    with open(os.path.join(workspace, "frontend/src/pages/analytics/AnalyticsPage.tsx"), "w") as f:
        f.write(_ANALYTICS_PAGE)
    with open(os.path.join(workspace, "frontend/src/pages/dashboard/SalesOverviewPage.tsx"), "w") as f:
        f.write(_SALES_PAGE)


def _commit(workspace: str, msg: str = "seed"):
    subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg, "--allow-empty"], cwd=workspace, capture_output=True)


def _confirm(run_id: str, workspace: str) -> dict:
    from app.api.agent_confirm import ConfirmRequest, _agent_confirm

    draft = {
        "status": "ok",
        "contract_id": "analytics.filter",
        "contract_version": 1,
        "interpretation_id": f"i-{run_id}",
        "proposed_actions": [
            {"verb": "create", "target_capability": "presentation.filter_panel",
             "contract_id": "analytics.filter", "params": {"filters": ["size"]}},
        ],
        "alternatives": [],
        "params_proposed": {"filters": ["size"]},
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
            contract_id="analytics.filter",
            contract_version=1,
            actions=[{
                "verb": "create",
                "target_capability": "presentation.filter_panel",
                "params": {"filters": ["size"]},
                "attach": {
                    "target": {"capability": "layout.page", "instance_label": "SalesOverviewPage"},
                },
            }],
            params={"filters": ["size"]},
            user_message="Add a filter by size in sales dashboard",
        ))
    finally:
        delete_run_state(run_id)


def _apply(workspace: str, artifacts: str, plan: dict) -> tuple:
    run_id = str(uuid.uuid4())
    ctx = RunContext(
        run_id=run_id,
        base_dir=workspace,
        workspace=workspace,
        artifacts=artifacts,
    )
    result = apply_engine(run_id, plan, ctx, dry_run=True)
    return result, ctx


# ── §8 / caso main: la seleccion de contrato NO se va al dashboard ─────────


class TestFilterContractSelection:
    def test_filter_by_size_in_sales_dashboard_selects_filter(self):
        assert _select_contract("Add a filter by size in sales dashboard") == "analytics.filter"

    def test_filter_the_sales_dashboard_selects_filter(self):
        assert _select_contract("Filter the sales dashboard by size") == "analytics.filter"

    def test_filter_with_preposition_target_selects_filter(self):
        assert _select_contract("Add a filter in sales dashboard") == "analytics.filter"
        assert _select_contract("Add a filter to the dashboard by region") == "analytics.filter"

    def test_filter_spanish_selects_filter(self):
        assert _select_contract("Añade filtros para el dashboard de ventas") == "analytics.filter"
        assert _select_contract("Filtra el dashboard de ventas por región") == "analytics.filter"

    def test_dashboard_value_requests_do_not_collide(self):
        # El dashboard sigue seleccionandose cuando no hay intencion de filtro.
        assert _select_contract("Update metrics to revenue and growth") == "dashboard.sales_overview"
        assert _select_contract("Add a KPI metric to the sales dashboard") == "dashboard.sales_overview"
        assert _select_contract("Remove the KPI row") == "dashboard.sales_overview"
        assert _select_contract("Remove the line chart") == "dashboard.sales_overview"
        assert _select_contract("Create a bar chart of revenue by quarter") == "analytics.chart_bar"


# ── Circuito completo: confirm con attach + FileOps del apply ──────────────


class TestFilterApplyFileOps:
    def test_filter_create_confirm_and_apply(self, e2e_workspace, artifacts_dir):
        _seed(e2e_workspace)
        _commit(e2e_workspace)
        out = _confirm(str(uuid.uuid4()), e2e_workspace)
        assert out["status"] == "ok"

        plan = out["plan"]
        create = [a for a in plan["actions"] if a["target_capability"] == "presentation.filter_panel"]
        assert len(create) == 1
        action = create[0]

        # binding auto_unique confirmado: fuente + mapeo filters→_pageData.filters
        binding = action["binding"]
        assert binding["source"]["kind"] == "hook"
        assert binding["source"]["ref"] == "useDashboardData"
        mappings = {m["prop"]: m["from_field"] for m in binding["mapping"]}
        assert mappings.get("filters") == "_pageData.filters"

        # attach del plan: el WHAT va al dashboard SalesOverviewPage
        attach = action.get("attach") or {}
        assert attach.get("target", {}).get("capability") == "layout.page"
        assert attach.get("target", {}).get("instance_label") == "SalesOverviewPage"

        result, _ = _apply(e2e_workspace, artifacts_dir, plan)
        assert result["execution"]["status"] == "ok"
        assert result["execution"].get("conflict") is None

        ops = result["execution"]["operations"]

        create_ops = [op for op in ops if op["action"] == "create"]
        assert len(create_ops) == 1, f"no invented FileOps, got {[op['path'] for op in ops]}"
        assert create_ops[0]["path"].endswith("FilterPanel.tsx")
        assert "filters" in create_ops[0]["content"]

        modify_ops = [op for op in ops if op["action"] == "modify"]
        assert len(modify_ops) == 1, f"no invented FileOps, got {[op['path'] for op in ops]}"
        assert modify_ops[0]["path"].endswith("frontend/src/pages/dashboard/SalesOverviewPage.tsx")
        assert "FilterPanel" in modify_ops[0]["content"]