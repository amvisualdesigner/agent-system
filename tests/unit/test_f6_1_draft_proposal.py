"""Fase 6.1 — Requirement → Discovery → Proposal integrado en el draft.

Cubre el bloque B de la subfase: la cadena determinista se ejecuta contra el
registry real (data_access.json) + el contrato real + un worktree sembrado,
y adjunta `binding_proposal` al draft sin que el LLM participe.

Invariantes bloqueadas:
  * LLM ≠ DataBinding authority: el draft solo lo ENRIQUECE, no lo elige.
  * registry ≠ confirmed binding authority: el registry es evidencia previa.
  * matriz 0/1/N: 0 → unresolved, 1 → auto_unique, N → needs_choice.
  * sin requisito declarado → sin propuesta (no se fabrica conexión).
"""

from __future__ import annotations

import os

from app.binding.draft_proposal import (
    BINDING_GATING_VERBS,
    build_action_binding_proposal,
    component_for_capability,
    enrich_draft_binding_proposals,
    read_source_files,
)
from app.contracts.skill_registry import get_contract


def _seed(tmp_path, files: dict[str, str]) -> str:
    for rel, content in files.items():
        path = os.path.join(str(tmp_path), rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return str(tmp_path)


_FILTER_PAGE = (
    "import { useDashboardData } from '@/hooks/useDashboardData';\n"
    "import { FilterPanel } from '@/components/FilterPanel';\n"
    "export function AnalyticsPage() {\n"
    "  const { filters } = useDashboardData();\n"
    "  return <FilterPanel filters={filters} />;\n"
    "}\n"
)


# ── Filtro canónico: auto_unique sin rediscovery ──────────────────────────


def test_filter_panel_reaches_auto_unique(tmp_path):
    ws = _seed(tmp_path, {"src/pages/analytics/AnalyticsPage.tsx": _FILTER_PAGE})
    draft = {
        "contract_id": "analytics.filter",
        "contract_version": 1,
        "proposed_actions": [
            {"verb": "create", "target_capability": "presentation.filter_panel"}
        ],
    }

    summary = enrich_draft_binding_proposals(draft, workspace=ws)

    assert [s["status"] for s in summary] == ["auto_unique"]
    proposal = draft["proposed_actions"][0]["binding_proposal"]
    assert proposal["status"] == "auto_unique"
    assert proposal["target_component"] == "FilterPanel"
    assert proposal["source"]["ref"]
    assert proposal["mapping"] == [
        {
            "prop": "filters",
            "from_field": "_pageData.filters",
            "required": False,
            "transform": "identity",
        }
    ]
    assert draft["binding_decisions_pending"] == []


def test_filter_panel_auto_unique_survives_empty_file_contents():
    """La slice declarada basta: 1 candidata inequívoca sin ficheros."""
    from app.signature.prop_mapper import load_page_data_source

    proposal = build_action_binding_proposal(
        "FilterPanel",
        contract=get_contract("analytics.filter", 1),
        page_data_source=load_page_data_source(),
    )
    assert proposal is not None and proposal.status == "auto_unique"
    binding = proposal.to_binding()
    assert binding is not None and binding.source.ref


def test_filter_panel_auto_unique_without_workspace(tmp_path):
    draft = {
        "contract_id": "analytics.filter",
        "contract_version": 1,
        "proposed_actions": [
            {"verb": "create", "target_capability": "presentation.filter_panel"}
        ],
    }
    summary = enrich_draft_binding_proposals(draft, workspace=None)
    assert [s["status"] for s in summary] == ["auto_unique"]


# ── Ambigüedad: N candidatas → decisión humana observable ────────────────


def test_two_hooks_yield_needs_choice(tmp_path):
    ws = _seed(tmp_path, {
        "src/pages/analytics/AnalyticsPage.tsx": _FILTER_PAGE,
        "src/pages/analytics/OtherPage.tsx": (
            "import { useFilterState } from '@/hooks/useFilterState';\n"
            "export function Other() {\n"
            "  const { filters } = useFilterState();\n"
            "  return <FilterPanel filters={filters} />;\n"
            "}\n"
        ),
    })
    draft = {
        "contract_id": "analytics.filter",
        "contract_version": 1,
        "proposed_actions": [
            {"verb": "create", "target_capability": "presentation.filter_panel"}
        ],
    }

    summary = enrich_draft_binding_proposals(draft, workspace=ws)

    assert [s["status"] for s in summary] == ["needs_choice"]
    proposal = draft["proposed_actions"][0]["binding_proposal"]
    assert proposal["source"] is None
    assert proposal["mapping"] == []
    assert proposal["provenance"]["candidate_count"] == 2
    assert draft["binding_decisions_pending"][0]["status"] == "needs_choice"


# ── Sin requisito declarado → sin propuesta ───────────────────────────────


def test_component_without_data_requirement_gets_no_proposal(tmp_path):
    ws = _seed(tmp_path, {"src/pages/dashboard/Page.tsx": "export const Page = () => null;\n"})
    draft = {
        "contract_id": "dashboard.sales_overview",
        "contract_version": 1,
        "proposed_actions": [
            {"verb": "create", "target_capability": "layout.page"}
        ],
    }

    summary = enrich_draft_binding_proposals(draft, workspace=ws)

    assert summary == []
    assert "binding_proposal" not in draft["proposed_actions"][0]
    assert "binding_proposals" not in draft


# ── remove/keep no exigen binding ─────────────────────────────────────────


def test_non_materializing_verbs_are_not_gated(tmp_path):
    ws = _seed(tmp_path, {"src/pages/analytics/AnalyticsPage.tsx": _FILTER_PAGE})
    draft = {
        "contract_id": "analytics.filter",
        "contract_version": 1,
        "proposed_actions": [
            {"verb": "remove", "target_capability": "presentation.filter_panel"},
            {"verb": "keep", "target_capability": "presentation.filter_panel"},
        ],
    }

    summary = enrich_draft_binding_proposals(draft, workspace=ws)

    assert summary == []
    assert all("binding_proposal" not in a for a in draft["proposed_actions"])
    assert BINDING_GATING_VERBS == frozenset({"create", "modify", "transform"})


# ── Casos ambiguos del contrato: nunca auto_unique ────────────────────────


def test_kpi_row_stays_unresolved_without_binding(tmp_path):
    ws = _seed(tmp_path, {
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
    })
    draft = {
        "contract_id": "dashboard.sales_overview",
        "contract_version": 1,
        "proposed_actions": [
            {"verb": "create", "target_capability": "presentation.kpi_row"}
        ],
    }

    summary = enrich_draft_binding_proposals(draft, workspace=ws)

    assert [s["status"] for s in summary] == ["unresolved"]
    proposal = draft["proposed_actions"][0]["binding_proposal"]
    assert proposal["source"] is None
    assert proposal["mapping"] == []
    assert draft["binding_decisions_pending"][0]["status"] == "unresolved"


def test_timeseries_stays_unresolved_without_binding(tmp_path):
    ws = _seed(tmp_path, {
        "src/pages/dashboard/Page.tsx": (
            "import { useDashboardData } from '@/hooks/useDashboardData';\n"
            "export function Page() {\n"
            "  const { kpiData, chartData, filters } = useDashboardData();\n"
            "  return null;\n"
            "}\n"
        ),
    })
    draft = {
        "contract_id": "dashboard.sales_overview",
        "contract_version": 1,
        "proposed_actions": [
            {"verb": "modify", "target_capability": "presentation.timeseries"}
        ],
    }

    summary = enrich_draft_binding_proposals(draft, workspace=ws)

    assert [s["status"] for s in summary] == ["unresolved"]
    proposal = draft["proposed_actions"][0]["binding_proposal"]
    assert proposal["source"] is None
    assert proposal["mapping"] == []


# ── Autoridad: el LLM no puede aportar el binding ─────────────────────────


def test_draft_enrichment_is_the_only_source_of_the_proposal(tmp_path):
    ws = _seed(tmp_path, {"src/pages/analytics/AnalyticsPage.tsx": _FILTER_PAGE})
    draft = {
        "contract_id": "analytics.filter",
        "contract_version": 1,
        "proposed_actions": [
            {"verb": "create", "target_capability": "presentation.filter_panel"}
        ],
    }
    enrich_draft_binding_proposals(draft, workspace=ws)

    # Nada del LLM queda en la propuesta: ni source ni mapping.
    proposal = draft["proposed_actions"][0]["binding_proposal"]
    assert set(proposal) == {
        "status", "target_component", "source", "schema", "mapping",
        "evidence", "provenance",
    }
    assert all("ref" in e and e["ref"] for e in proposal["evidence"])


# ── Auxiliares ────────────────────────────────────────────────────────────


def test_component_for_capability_is_exact_and_single():
    contract = get_contract("analytics.filter", 1)
    assert component_for_capability(contract, "presentation.filter_panel") == "FilterPanel"
    assert component_for_capability(contract, "presentation.unknown") is None
    assert component_for_capability(contract, "") is None
    assert component_for_capability(None, "presentation.filter_panel") is None


def test_read_source_files_skips_vendored_dirs(tmp_path):
    ws = _seed(tmp_path, {
        "src/a.tsx": "export const a = 1;\n",
        "node_modules/pkg/b.tsx": "export const b = 2;\n",
        "dist/c.tsx": "export const c = 3;\n",
        "src/style.css": "body{}",
    })
    files = read_source_files(ws)
    assert sorted(files) == ["src/a.tsx"]
    assert read_source_files(None) == {}
    assert read_source_files(os.path.join(ws, "missing")) == {}


# ── Bloque D — gate de confirmación (decisión humana, no autoría) ─────────

from app.binding.draft_proposal import (  # noqa: E402
    BINDING_CHOICE_PENDING,
    BINDING_NOT_FROM_PROPOSAL,
    BINDING_UNRESOLVED,
    resolve_confirmed_binding,
)


def _proposal_for(ws, contract_id, capability, verb="create"):
    draft = {
        "contract_id": contract_id,
        "contract_version": 1,
        "proposed_actions": [{"verb": verb, "target_capability": capability}],
    }
    enrich_draft_binding_proposals(draft, workspace=ws)
    return draft["proposed_actions"][0].get("binding_proposal")


def _auto_unique_proposal(tmp_path):
    ws = _seed(tmp_path, {"src/pages/analytics/AnalyticsPage.tsx": _FILTER_PAGE})
    return ws, _proposal_for(ws, "analytics.filter", "presentation.filter_panel")


def _needs_choice_proposal(tmp_path):
    ws = _seed(tmp_path, {
        "src/pages/analytics/AnalyticsPage.tsx": _FILTER_PAGE,
        "src/pages/analytics/OtherPage.tsx": (
            "import { useFilterState } from '@/hooks/useFilterState';\n"
            "export function Other() {\n"
            "  const { filters } = useFilterState();\n"
            "  return <FilterPanel filters={filters} />;\n"
            "}\n"
        ),
    })
    return ws, _proposal_for(ws, "analytics.filter", "presentation.filter_panel")


def test_auto_unique_materializes_proposal_without_client_binding(tmp_path):
    _ws, proposal = _auto_unique_proposal(tmp_path)

    binding, error = resolve_confirmed_binding(None, proposal)

    assert error is None
    assert binding is not None
    assert binding.source.ref
    assert [m.prop for m in binding.mapping] == ["filters"]
    assert binding.mapping[0].from_field == "_pageData.filters"


def test_auto_unique_accepts_identical_payload_but_uses_proposal(tmp_path):
    _ws, proposal = _auto_unique_proposal(tmp_path)

    submitted = {k: proposal[k] for k in ("source", "schema", "mapping")}
    binding, error = resolve_confirmed_binding(submitted, proposal)

    assert error is None and binding is not None
    assert binding.to_dict()["source"] == proposal["source"]
    assert binding.to_dict()["schema"] == proposal["schema"]
    assert binding.to_dict()["mapping"] == proposal["mapping"]


def test_auto_unique_rejects_invented_source(tmp_path):
    _ws, proposal = _auto_unique_proposal(tmp_path)

    submitted = dict(proposal)
    submitted["source"] = {"kind": "hook", "ref": "useFakeData"}
    binding, error = resolve_confirmed_binding(submitted, proposal)

    assert binding is None and error == BINDING_NOT_FROM_PROPOSAL


def test_auto_unique_rejects_invented_from_field(tmp_path):
    _ws, proposal = _auto_unique_proposal(tmp_path)

    submitted = dict(proposal)
    submitted["mapping"] = [{"prop": "filters", "from_field": "_pageData.other", "transform": "identity"}]
    binding, error = resolve_confirmed_binding(submitted, proposal)

    assert binding is None and error == BINDING_NOT_FROM_PROPOSAL


def test_needs_choice_requires_human_selection(tmp_path):
    _ws, proposal = _needs_choice_proposal(tmp_path)
    assert proposal["status"] == "needs_choice"

    binding, error = resolve_confirmed_binding(None, proposal)

    assert binding is None and error == BINDING_CHOICE_PENDING


def test_needs_choice_accepts_proven_candidate_selection(tmp_path):
    _ws, proposal = _needs_choice_proposal(tmp_path)
    candidates = proposal["provenance"]["candidates"]
    chosen = next(c for c in candidates if c["ref"] == "useDashboardData")

    submitted = {
        "source": {"kind": "hook", "ref": chosen["ref"]},
        "mapping": [{"prop": "filters", "from_field": "_pageData.filters"}],
    }
    binding, error = resolve_confirmed_binding(submitted, proposal)

    assert error is None and binding is not None
    assert binding.source.kind == "hook" and binding.source.ref == "useDashboardData"
    assert binding.mapping[0].from_field == "_pageData.filters"
    assert binding.mapping[0].transform == "identity"


def test_needs_choice_rejects_source_outside_candidates(tmp_path):
    _ws, proposal = _needs_choice_proposal(tmp_path)

    submitted = {
        "source": {"kind": "hook", "ref": "useFakeData"},
        "mapping": [{"prop": "filters", "from_field": "_pageData.filters"}],
    }
    binding, error = resolve_confirmed_binding(submitted, proposal)

    assert binding is None and error == BINDING_NOT_FROM_PROPOSAL


def test_needs_choice_rejects_invented_from_field(tmp_path):
    _ws, proposal = _needs_choice_proposal(tmp_path)

    submitted = {
        "source": {"kind": "hook", "ref": "useDashboardData"},
        "mapping": [{"prop": "filters", "from_field": "_pageData.other"}],
    }
    binding, error = resolve_confirmed_binding(submitted, proposal)

    assert binding is None and error == BINDING_NOT_FROM_PROPOSAL


def test_unresolved_blocks_even_with_submitted_binding(tmp_path):
    ws = _seed(tmp_path, {
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
    })
    proposal = _proposal_for(ws, "dashboard.sales_overview", "presentation.kpi_row")
    assert proposal["status"] == "unresolved"

    submitted = {"source": {"kind": "slice", "ref": "kpiData", "selector": "kpiData"},
                 "mapping": [{"prop": "metrics", "from_field": "_pageData.kpiData"}]}
    binding, error = resolve_confirmed_binding(submitted, proposal)

    assert binding is None and error == BINDING_UNRESOLVED


def test_no_proposal_never_allows_an_arbitrary_binding():
    submitted = {"source": {"kind": "hook", "ref": "useFake"},
                 "mapping": [{"prop": "filters", "from_field": "_pageData.filters"}]}
    binding, error = resolve_confirmed_binding(submitted, None)
    assert binding is None and error == BINDING_NOT_FROM_PROPOSAL


def test_no_proposal_and_no_binding_means_no_requirement():
    binding, error = resolve_confirmed_binding(None, None)
    assert binding is None and error is None
