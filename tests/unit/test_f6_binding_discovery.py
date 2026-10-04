"""Subfase C — deterministic candidate discovery via the five closed relations."""

from __future__ import annotations

import json

import pytest

from app.binding.discovery import (
    CLOSED_RELATIONS,
    discover_binding_candidates,
    scan_calls,
    scan_imports,
)
from app.binding.requirement import build_binding_requirement
from app.intent.models import BindingRequirement
from app.signature.prop_mapper import load_page_data_source, load_v4_bindings


@pytest.fixture(scope="module")
def v4():
    return load_v4_bindings()


@pytest.fixture(scope="module")
def page_ds():
    return load_page_data_source()


@pytest.fixture
def filter_contract():
    return _contract_for("FilterPanel", {"filters": "filters"})


def _contract_for(component: str, slot_map: dict[str, str], input_schema: dict | None = None):
    capability = f"analytics.{component.lower()}"
    return _Contract(
        ast_template={
            "capabilities": {component: capability},
            "slots": [{"type": component, "props": dict(slot_map)}],
        },
        capability_param_map={capability: dict(slot_map)},
        input_schema=input_schema,
    )


class _Contract:
    """Minimal stand-in exposing the same evidence surface as SkillContract."""

    def __init__(self, ast_template: dict, capability_param_map: dict, input_schema: dict | None = None):
        self.ast_template = ast_template
        self.capability_param_map = capability_param_map
        self.input_schema = input_schema


def _page_tsx(fields: str = "kpiData, chartData, filters", consumer: str = "") -> str:
    return (
        "import React from 'react';\n"
        "import { useDashboardData } from '../../hooks/useDashboardData';\n"
        f"export const Page = () => {{\n  const {{ {fields} }} = useDashboardData();\n"
        f"{consumer}"
        "  return null;\n};\n"
    )


# ── relation inventory ───────────────────────────────────────────────────


def test_closed_relations_are_exactly_the_five():
    assert CLOSED_RELATIONS == (
        "declared_dataslice",
        "existing_registry_binding",
        "existing_import",
        "existing_call",
        "contract_slot_prop",
    )


def test_no_relation_is_a_ranking():  # relations are recorded, never resolved
    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=_contract_for("FilterPanel", {"filters": "filters"}),
        target_file_contents={"Page.tsx": _page_tsx()},
    )
    assert d.candidates
    for c in d.candidates:
        assert set(c.relations).issubset(CLOSED_RELATIONS)


# ── scanners ─────────────────────────────────────────────────────────────


def test_scan_imports_named_and_default():
    out = dict(scan_imports("import { a, b as c } from 'm';\nimport d from 'n';"))
    assert out == {"a": "m", "c": "m", "d": "n"}


def test_scan_imports_skips_type_only():
    assert "X" not in dict(scan_imports("import type { X } from 'm';\nimport { Y } from 'm';"))


def test_scan_calls_destructured_with_alias_and_default():
    calls = dict(scan_calls("const { a, b: c, d = 1 } = hook();"))
    assert calls["hook"] == ("a", "c", "d")


def test_scan_calls_deterministic():
    content = "const { z, a } = hook();"
    assert scan_calls(content) == scan_calls(content)


# ── §8 FilterPanel: the real case ─────────────────────────────────────────


def test_filter_panel_real_registry_has_dataslice_relation(page_ds, v4, filter_contract):
    """The real data_access.json slice is found and feeds filters."""
    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=filter_contract,
        v4_bindings=v4,
        page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page_tsx()},
    )
    assert d.candidate_count == 1
    c = d.candidates[0]
    assert ("filters", "filters") in c.prop_field_links
    assert "declared_dataslice" in c.relations
    assert "existing_import" in c.relations
    assert "existing_call" in c.relations


def test_filter_panel_slice_unified_with_hook_by_proof_not_ranking(
    page_ds, v4, filter_contract
):
    """Slice selector rooted at a destructured call field == same physical source."""
    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=filter_contract,
        v4_bindings=v4,
        page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page_tsx()},
    )
    # Exactly one candidate: the slice did not become a second, competing source.
    assert [c.source.kind for c in d.candidates] == ["hook"]
    assert d.candidates[0].source.ref == "useDashboardData"


def test_filter_panel_without_page_files_uses_declared_dataslice(
    page_ds, v4, filter_contract
):
    """No page files: the registry slice is the candidate. The slot is not a source."""
    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=filter_contract,
        v4_bindings=v4,
        page_data_source=page_ds,
    )
    assert d.candidate_count == 1
    c = d.candidates[0]
    assert c.source.kind == "slice"
    assert c.relations == ("declared_dataslice",)
    # The contract slot supplied a link, never a source identity.
    assert "contract_slot_prop" not in c.relations


# ── each relation's real contribution ────────────────────────────────────


def test_declared_dataslice_alone_yields_slice_candidate(filter_contract):
    class _Slice:
        component = "FilterPanel"
        target_prop = "filters"
        selector = "filters"

    class _DS:
        slices = (_Slice(),)

    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=filter_contract,
        page_data_source=_DS(),
    )
    assert d.candidate_count == 1
    assert d.candidates[0].source.kind == "slice"
    assert d.candidates[0].relations == ("declared_dataslice",)


def test_declared_dataslice_not_rooted_in_call_stays_separate(filter_contract):
    """An unprovable slice is NOT merged into the hook: two candidates, no ranking."""

    class _Slice:
        component = "FilterPanel"
        target_prop = "filters"
        selector = "deep.unseen"

    class _DS:
        slices = (_Slice(),)

    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=filter_contract,
        page_data_source=_DS(),
        target_file_contents={"Page.tsx": _page_tsx(fields="kpiData")},
    )
    kinds = sorted(c.source.kind for c in d.candidates)
    assert kinds == ["hook", "slice"]


def test_existing_import_alone_is_not_a_candidate(filter_contract):
    """Import proves reachability but no fields -> no schema evidence."""
    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=filter_contract,
        target_file_contents={"Page.tsx": "import { useDashboardData } from '../../hooks/useDashboardData';\n"},
    )
    assert d.candidate_count == 0


def test_existing_call_to_unimported_symbol_is_not_a_candidate(filter_contract):
    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=filter_contract,
        target_file_contents={"Page.tsx": "const { filters } = mysteryHook();"},
    )
    assert d.candidate_count == 0


def test_existing_call_yields_candidate_via_contract_slot(filter_contract):
    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=filter_contract,
        target_file_contents={"Page.tsx": _page_tsx(fields="kpiData, filters")},
    )
    assert d.candidate_count == 1
    assert d.candidates[0].relations == ("contract_slot_prop", "existing_call", "existing_import")


def test_existing_registry_binding_contributes_evidence(v4):
    class _B:
        from_field = "metrics"

    d = discover_binding_candidates(
        BindingRequirement(target_component="KpiRow", required_props=("data",)),
        v4_bindings={"KpiRow": {"data": _B()}},
        target_file_contents={"Page.tsx": _page_tsx(fields="kpiData, metrics")},
    )
    assert d.candidate_count == 1
    assert "existing_registry_binding" in d.candidates[0].relations
    assert ("data", "metrics") in d.candidates[0].prop_field_links


# ── qualification ────────────────────────────────────────────────────────


def test_candidate_needs_every_required_prop(filter_contract):
    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters", "sort")),
        contract=_contract_for("FilterPanel", {"filters": "filters"}),
        target_file_contents={"Page.tsx": _page_tsx(fields="filters")},
    )
    assert d.candidate_count == 0


def test_incompatibility_on_required_prop_excludes_candidate(filter_contract):
    req = BindingRequirement(
        target_component="FilterPanel",
        required_props=("filters",),
        incompatibilities=("conflicting_shape_declarations:FilterPanel.filters",),
    )
    d = discover_binding_candidates(
        req, contract=filter_contract, target_file_contents={"Page.tsx": _page_tsx()}
    )
    assert d.candidate_count == 0
    assert d.requirement_incompatibilities == (
        "conflicting_shape_declarations:FilterPanel.filters",
    )


def test_incompatibility_on_other_component_does_not_exclude(filter_contract):
    req = BindingRequirement(
        target_component="FilterPanel",
        required_props=("filters",),
        incompatibilities=("conflicting_shape_declarations:MetricCard.value",),
    )
    d = discover_binding_candidates(
        req, contract=filter_contract, target_file_contents={"Page.tsx": _page_tsx()}
    )
    assert d.candidate_count == 1


def test_excluded_candidates_are_reported_not_dropped(filter_contract):
    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters", "sort")),
        contract=filter_contract,
        target_file_contents={"Page.tsx": _page_tsx(fields="filters")},
    )
    assert d.candidate_count == 0
    assert len(d.excluded_candidates) == 1


# ── guardrails ───────────────────────────────────────────────────────────


def test_discovery_makes_no_ambiguity_resolution(filter_contract):
    """Two provable sources -> 2 candidates. C never picks one."""
    content = (
        "import { useDashboardData } from '../../hooks/useDashboardData';\n"
        "import { useSales } from '../../hooks/useSales';\n"
        "const { filters } = useDashboardData();\n"
        "const { filters } = useSales();\n"
    )
    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=filter_contract,
        target_file_contents={"Page.tsx": content},
    )
    assert d.candidate_count == 2
    assert "status" not in BindingDiscovery_fields(d)
    assert sorted(c.source.ref for c in d.candidates) == ["useDashboardData", "useSales"]


def BindingDiscovery_fields(d) -> set[str]:
    return set(d.__dataclass_fields__)


def test_no_data_binding_or_mapping_is_produced(filter_contract):
    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=filter_contract,
        target_file_contents={"Page.tsx": _page_tsx()},
    )
    for c in d.candidates:
        assert not hasattr(c, "mapping")
        assert not hasattr(c, "binding")


def test_order_independent_results(filter_contract):
    req = BindingRequirement(target_component="FilterPanel", required_props=("filters",))
    files_a = {"Page.tsx": _page_tsx(), "Other.tsx": "import { x } from 'y';\n"}
    files_b = {"Other.tsx": "import { x } from 'y';\n", "Page.tsx": _page_tsx()}
    a = discover_binding_candidates(req, contract=filter_contract, target_file_contents=files_a)
    b = discover_binding_candidates(req, contract=filter_contract, target_file_contents=files_b)
    assert a.candidates == b.candidates


def test_pure_no_mutation_of_inputs(v4):
    before = json.dumps({k: {p: vars(s) for p, s in b.items()} for k, b in v4.items()}, default=str, sort_keys=True)
    contract = _contract_for("FilterPanel", {"filters": "filters"})
    discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=contract,
        v4_bindings=v4,
        target_file_contents={"Page.tsx": _page_tsx()},
    )
    after = json.dumps({k: {p: vars(s) for p, s in b.items()} for k, b in v4.items()}, default=str, sort_keys=True)
    assert before == after
    assert contract.capability_param_map == {"analytics.filterpanel": {"filters": "filters"}}


def test_never_raises_on_garbage():
    class _Boom:
        @property
        def slices(self):
            raise RuntimeError("nope")

    class _C(_Contract):
        def __init__(self):
            super().__init__(
                ast_template={"capabilities": {"FilterPanel": "analytics.filter"}},
                capability_param_map={"analytics.filter": {"filters": None}},
            )

    d = discover_binding_candidates(
        BindingRequirement(target_component="FilterPanel", required_props=("filters",)),
        contract=_C(),
        page_data_source=_Boom(),
        target_file_contents={"Page.tsx": "}}} not js { const { = } = (;"},
    )
    assert d.candidate_count == 0


def test_empty_requirement_yields_no_candidates():
    d = discover_binding_candidates(BindingRequirement(target_component="X"))
    assert d.candidate_count == 0


def test_full_binding_requirement_still_accepted(v4, filter_contract):
    """Discovery consumes the real B output, not a hand-made requirement."""
    req = build_binding_requirement("FilterPanel", contract=filter_contract, v4_bindings=v4)
    assert req.required_props == ("filters",)
    d = discover_binding_candidates(
        req, contract=filter_contract, v4_bindings=v4, target_file_contents={"Page.tsx": _page_tsx()}
    )
    assert d.candidate_count == 1