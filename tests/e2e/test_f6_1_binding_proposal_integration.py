"""Fase 6.1 — integración Requirement → Discovery → Proposal → Confirmation.

Cubre los bloques C/D/E/F sobre el pipeline REAL (`/agent/interpret`'s draft +
`/agent/confirm`), con el registry v4 y el contrato reales:

  * 1 determinista  → FilterPanel (analytics.filter) alcanza auto_unique y el
    binding llega a `compiled_plan.actions[].binding` SIN rediscovery ni payload
    del cliente.
  * 0                → KpiRow/Timeseries quedan sin binding y bloquean como
    clarification (gate `binding_unresolved`).
  * N                → dos candidatas probadas exigen decisión humana explícita
    (gate `binding_choice_pending`) y la selección viaja a
    `IntentAction.binding`.
  * LLM/autoridad    → un binding inventado o sin propuesta se rechaza
    (`binding_not_from_proposal`).
  * registry NO materializa decisión humana → no hay fallback silencioso.
"""

import os
import shutil
import uuid

import pytest

from app.binding.draft_proposal import enrich_draft_binding_proposals
from app.config.settings import settings
from app.intent.models import RunPhase
from app.state.run_state import delete_run_state, load_run_state, save_run_state


def _seed_workspace(run_id: str, files: dict[str, str]) -> str:
    ws = os.path.join(settings.RUNS_DIR, run_id)
    shutil.rmtree(ws, ignore_errors=True)
    os.makedirs(ws, exist_ok=True)
    for rel, content in files.items():
        path = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return ws


def _persist_draft(run_id: str, ws: str | None, *actions: dict) -> dict:
    draft = {
        "status": "ok",
        "contract_id": actions[0]["contract_id"],
        "contract_version": 1,
        "interpretation_id": f"i-{run_id}",
        "proposed_actions": list(actions),
        "alternatives": [],
        "params_proposed": {},
        "worktree_capabilities": [],
    }
    enrich_draft_binding_proposals(draft, workspace=ws)
    save_run_state(run_id, {
        "phase": RunPhase.AWAITING_CONFIRMATION.value,
        "interpretation_draft": draft,
    })
    return draft


def _confirm(run_id: str, contract_id: str, actions: list[dict]) -> dict:
    from app.api.agent_confirm import ConfirmRequest, _agent_confirm

    return _agent_confirm(ConfirmRequest(
        run_id=run_id,
        interpretation_id=f"i-{run_id}",
        contract_id=contract_id,
        contract_version=1,
        actions=actions,
        params={},
        user_message="test",
    ))


_FILTER_PAGE = {
    "src/pages/analytics/AnalyticsPage.tsx": (
        "import { useDashboardData } from '@/hooks/useDashboardData';\n"
        "import { FilterPanel } from '@/components/FilterPanel';\n"
        "export function AnalyticsPage() {\n"
        "  const { filters } = useDashboardData();\n"
        "  return <FilterPanel filters={filters} />;\n"
        "}\n"
    ),
}

_AMBIGUOUS_PAGES = {
    **_FILTER_PAGE,
    "src/pages/analytics/OtherPage.tsx": (
        "import { useFilterState } from '@/hooks/useFilterState';\n"
        "export function Other() {\n"
        "  const { filters } = useFilterState();\n"
        "  return <FilterPanel filters={filters} />;\n"
        "}\n"
    ),
}

_DASHBOARD_PAGE = {
    "src/pages/dashboard/Page.tsx": (
        "import { useDashboardData } from '@/hooks/useDashboardData';\n"
        "export function Page() {\n"
        "  const { kpiData, chartData, filters } = useDashboardData();\n"
        "  return null;\n"
        "}\n"
    ),
    "src/pages/dashboard/components/KpiRow.tsx": (
        "export function KpiRow(props: { data: KpiItem[] }) {\n"
        "  return null;\n"
        "}\n"
    ),
}


# ── 1 candidata → auto_unique → binding en el Plan sin rediscovery ─────────


class TestAutoUniqueIntoLandedPlan:
    def test_filter_panel_binding_reaches_compiled_plan(self):
        run_id = str(uuid.uuid4())
        try:
            ws = _seed_workspace(run_id, _FILTER_PAGE)
            draft = _persist_draft(run_id, ws, {
                "verb": "create",
                "target_capability": "presentation.filter_panel",
                "contract_id": "analytics.filter",
            })
            proposal = draft["proposed_actions"][0]["binding_proposal"]
            assert proposal["status"] == "auto_unique"

            out = _confirm(run_id, "analytics.filter", [{
                "verb": "create",
                "target_capability": "presentation.filter_panel",
            }])
            assert out["status"] == "ok"
            assert out["plan"]["actions"][0]["binding"]["source"] == proposal["source"]
            assert out["plan"]["actions"][0]["binding"]["mapping"] == proposal["mapping"]

            saved = load_run_state(run_id)
            landed = saved["confirmed_intent"]["actions"][0]["binding"]
            assert landed is not None
            assert landed["source"]["kind"] == proposal["source"]["kind"]
            assert landed["source"]["ref"] == proposal["source"]["ref"]
            assert landed["mapping"][0]["from_field"] == "_pageData.filters"
            assert saved["binding_resolution"][0]["provenance"] == "auto_unique"
        finally:
            delete_run_state(run_id)

    def test_auto_unique_ignores_no_discovery_on_confirm(self, monkeypatch):
        run_id = str(uuid.uuid4())
        try:
            ws = _seed_workspace(run_id, _FILTER_PAGE)
            _persist_draft(run_id, ws, {
                "verb": "create",
                "target_capability": "presentation.filter_panel",
                "contract_id": "analytics.filter",
            })

            def boom(*_a, **_k):
                raise AssertionError("discovery must NOT run during confirm")

            monkeypatch.setattr(
                "app.binding.draft_proposal.discover_binding_candidates", boom
            )
            out = _confirm(run_id, "analytics.filter", [{
                "verb": "create",
                "target_capability": "presentation.filter_panel",
            }])
            assert out["status"] == "ok"
            assert out["plan"]["actions"][0]["binding"]["source"]["ref"]
        finally:
            delete_run_state(run_id)

    def test_confirmed_binding_survives_state_reload(self):
        run_id = str(uuid.uuid4())
        try:
            ws = _seed_workspace(run_id, _FILTER_PAGE)
            draft = _persist_draft(run_id, ws, {
                "verb": "create",
                "target_capability": "presentation.filter_panel",
                "contract_id": "analytics.filter",
            })
            binding_expected = draft["proposed_actions"][0]["binding_proposal"]

            _confirm(run_id, "analytics.filter", [{
                "verb": "create",
                "target_capability": "presentation.filter_panel",
            }])

            saved = load_run_state(run_id)
            assert saved["compiled_plan"]["actions"][0]["binding"]["source"] == \
                binding_expected["source"]
            assert saved["compiled_plan"]["actions"][0]["binding"]["mapping"] == \
                binding_expected["mapping"]
            assert "binding" in saved["confirmed_intent"]["actions"][0]
        finally:
            delete_run_state(run_id)


# ── 0 candidatas → unresolved → clarification, sin fallback al registry ────


class TestUnresolvedBlocksWithoutFallback:
    @pytest.mark.parametrize("capability", [
        "presentation.kpi_row",
        "presentation.timeseries",
    ])
    def test_unresolved_component_is_rejected_with_clarification(self, capability):
        run_id = str(uuid.uuid4())
        try:
            ws = _seed_workspace(run_id, _DASHBOARD_PAGE)
            draft = _persist_draft(run_id, ws, {
                "verb": "create",
                "target_capability": capability,
                "contract_id": "dashboard.sales_overview",
            })
            proposal = draft["proposed_actions"][0]["binding_proposal"]
            assert proposal["status"] == "unresolved"
            assert proposal["mapping"] == []

            out = _confirm(run_id, "dashboard.sales_overview", [{
                "verb": "create",
                "target_capability": capability,
            }])
            assert out["status"] == "rejected"
            assert out["gate"]["blocked"] is True
            assert out["gate"]["reason"] == "binding_unresolved"
            assert out["binding_proposal"]["status"] == "unresolved"
            # Ni el registry lo materializa en silencio:
            assert load_run_state(run_id).get("confirmed_intent") is None
        finally:
            delete_run_state(run_id)

    def test_remove_action_is_not_blocked_by_unresolved_binding(self):
        # remove NO materializa componente: exigir binding impediría eliminar
        # un componente ambiguo.
        run_id = str(uuid.uuid4())
        try:
            _seed_workspace(run_id, _DASHBOARD_PAGE)
            _persist_draft(run_id, None, {
                "verb": "remove",
                "target_capability": "presentation.kpi_row",
                "contract_id": "dashboard.sales_overview",
            })
            out = _confirm(run_id, "dashboard.sales_overview", [{
                "verb": "remove",
                "target_capability": "presentation.kpi_row",
            }])
            assert out["status"] == "ok"
            saved = load_run_state(run_id)
            assert saved["confirmed_intent"]["actions"][0]["binding"] is None
        finally:
            delete_run_state(run_id)


# ── N candidatas → decisión humana explícita (sin rediscovery) ─────────────


class TestNeedsChoiceHumanDecision:
    def test_needs_choice_without_selection_blocks(self):
        run_id = str(uuid.uuid4())
        try:
            ws = _seed_workspace(run_id, _AMBIGUOUS_PAGES)
            draft = _persist_draft(run_id, ws, {
                "verb": "create",
                "target_capability": "presentation.filter_panel",
                "contract_id": "analytics.filter",
            })
            proposal = draft["proposed_actions"][0]["binding_proposal"]
            assert proposal["status"] == "needs_choice"
            assert proposal["provenance"]["candidate_count"] == 2

            out = _confirm(run_id, "analytics.filter", [{
                "verb": "create",
                "target_capability": "presentation.filter_panel",
            }])
            assert out["status"] == "rejected"
            assert out["gate"]["reason"] == "binding_choice_pending"
            assert out["binding_proposal"]["status"] == "needs_choice"
            assert load_run_state(run_id).get("confirmed_intent") is None
        finally:
            delete_run_state(run_id)

    def test_human_selection_materializes_selected_candidate(self):
        run_id = str(uuid.uuid4())
        try:
            ws = _seed_workspace(run_id, _AMBIGUOUS_PAGES)
            draft = _persist_draft(run_id, ws, {
                "verb": "create",
                "target_capability": "presentation.filter_panel",
                "contract_id": "analytics.filter",
            })
            proposal = draft["proposed_actions"][0]["binding_proposal"]

            out = _confirm(run_id, "analytics.filter", [{
                "verb": "create",
                "target_capability": "presentation.filter_panel",
                "binding": {
                    "source": {"kind": "hook", "ref": "useDashboardData"},
                    "mapping": [{"prop": "filters", "from_field": "_pageData.filters"}],
                },
            }])
            assert out["status"] == "ok"
            landed = out["plan"]["actions"][0]["binding"]
            assert landed["source"] == {"kind": "hook", "ref": "useDashboardData"}
            assert landed["mapping"][0]["from_field"] == "_pageData.filters"
            assert landed["mapping"][0]["transform"] == "identity"

            saved = load_run_state(run_id)
            res = saved["binding_resolution"][0]
            assert res["provenance"] == "needs_choice"
            assert res["source"]["ref"] == "useDashboardData"
            assert saved["confirmed_intent"]["actions"][0]["binding"]["source"]["ref"] == \
                "useDashboardData"
        finally:
            delete_run_state(run_id)

    def test_human_selection_does_not_rediscover(self, monkeypatch):
        run_id = str(uuid.uuid4())
        try:
            ws = _seed_workspace(run_id, _AMBIGUOUS_PAGES)
            _persist_draft(run_id, ws, {
                "verb": "create",
                "target_capability": "presentation.filter_panel",
                "contract_id": "analytics.filter",
            })

            def boom(*_a, **_k):
                raise AssertionError("discovery must NOT run during confirm")

            monkeypatch.setattr(
                "app.binding.draft_proposal.discover_binding_candidates", boom
            )
            out = _confirm(run_id, "analytics.filter", [{
                "verb": "create",
                "target_capability": "presentation.filter_panel",
                "binding": {
                    "source": {"kind": "hook", "ref": "useFilterState"},
                    "mapping": [{"prop": "filters", "from_field": "_pageData.filters"}],
                },
            }])
            assert out["status"] == "ok"
            assert out["plan"]["actions"][0]["binding"]["source"]["ref"] == "useFilterState"
        finally:
            delete_run_state(run_id)


# ── Autoridad: bindings inventados se rechazan ─────────────────────────────


class TestRejectsInventedBindings:
    def test_arbitrary_binding_with_no_proposal_is_rejected(self):
        run_id = str(uuid.uuid4())
        try:
            ws = _seed_workspace(run_id, _FILTER_PAGE)
            draft = _persist_draft(run_id, ws, {
                "verb": "create",
                "target_capability": "presentation.filter_panel",
                "contract_id": "analytics.filter",
            })
            # Una propuesta SI existe; el binding inventado debe chocar con ella.
            assert draft["proposed_actions"][0]["binding_proposal"]["status"] == "auto_unique"

            out = _confirm(run_id, "analytics.filter", [{
                "verb": "create",
                "target_capability": "presentation.filter_panel",
                "binding": {
                    "source": {"kind": "service", "ref": "AppDataService.fetchAll"},
                    "mapping": [{"prop": "filters", "from_field": "_pageData.other"}],
                },
            }])
            assert out["status"] == "rejected"
            assert out["gate"]["reason"] == "binding_not_from_proposal"
            assert load_run_state(run_id).get("confirmed_intent") is None
        finally:
            delete_run_state(run_id)

    def test_binding_without_requirement_is_rejected(self):
        run_id = str(uuid.uuid4())
        try:
            ws = _seed_workspace(run_id, _DASHBOARD_PAGE)
            _persist_draft(run_id, ws, {
                "verb": "create",
                "target_capability": "layout.page",
                "contract_id": "dashboard.sales_overview",
            })
            out = _confirm(run_id, "dashboard.sales_overview", [{
                "verb": "create",
                "target_capability": "layout.page",
                "binding": {
                    "source": {"kind": "hook", "ref": "useFake"},
                    "mapping": [{"prop": "title", "from_field": "_pageData.title"}],
                },
            }])
            assert out["status"] == "rejected"
            assert out["gate"]["reason"] == "binding_not_from_proposal"
        finally:
            delete_run_state(run_id)


def test_interpret_draft_persists_proposals_between_interpret_and_confirm():
    """E: la propuesta viaja persistida en interpretation_draft, no en memoria."""
    run_id = str(uuid.uuid4())
    try:
        ws = _seed_workspace(run_id, _FILTER_PAGE)
        draft = _persist_draft(run_id, ws, {
            "verb": "create",
            "target_capability": "presentation.filter_panel",
            "contract_id": "analytics.filter",
        })
        persisted = load_run_state(run_id)["interpretation_draft"]
        assert persisted["proposed_actions"][0]["binding_proposal"] == \
            draft["proposed_actions"][0]["binding_proposal"]

        # El worktree NO vuelve a comprobarse en confirm: se borran las
        # evidencias y la propuesta persistida sigue mandando.
        shutil.rmtree(ws, ignore_errors=True)
        out = _confirm(run_id, "analytics.filter", [{
            "verb": "create",
            "target_capability": "presentation.filter_panel",
        }])
        assert out["status"] == "ok"
        assert out["plan"]["actions"][0]["binding"]["source"]["ref"]
    finally:
        delete_run_state(run_id)