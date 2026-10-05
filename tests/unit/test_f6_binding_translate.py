"""Subfase E — DataBinding confirmado como autoridad semantica."""

from __future__ import annotations

import pytest

from app.binding.discovery import discover_binding_candidates
from app.binding.mapping import derive_binding_proposal
from app.binding.requirement import build_binding_requirement
from app.binding.resolver import resolve
from app.binding.translate import (
    PAGE_DATA_PREFIX,
    UntranslatableBinding,
    apply_confirmed_bindings,
    confirmed_bindings_from_actions,
    translate_confirmed_binding,
)
from app.graphir.backends.react_backend import JSVariable
from app.intent.models import (
    DataBinding,
    DataMappingEntry,
    DataSourceRef,
    IntentAction,
)
from app.signature.prop_mapper import load_page_data_source, load_v4_bindings


class _Contract:
    def __init__(self, ast_template, capability_param_map, input_schema=None):
        self.ast_template = ast_template
        self.capability_param_map = capability_param_map
        self.input_schema = input_schema


def _contract_for(component, slot_map, input_schema=None):
    cap = f"analytics.{component.lower()}"
    return _Contract(
        ast_template={
            "capabilities": {component: cap},
            "slots": [{"type": component, "props": dict(slot_map)}],
        },
        capability_param_map={cap: dict(slot_map)},
        input_schema=input_schema,
    )


def _page(fields="kpiData, chartData, filters"):
    return (
        "import { useDashboardData } from '../../hooks/useDashboardData';\n"
        f"const {{ {fields} }} = useDashboardData();\n"
    )


@pytest.fixture(scope="module")
def v4():
    return load_v4_bindings()


@pytest.fixture(scope="module")
def page_ds():
    return load_page_data_source()


def _filter_binding():
    return DataBinding(
        source=DataSourceRef(kind="hook", ref="useDashboardData"),
        schema=None,
        mapping=(
            DataMappingEntry(
                prop="filters", from_field="_pageData.filters", transform="identity"
            ),
        ),
    )


# ── the translation is an identity, not a choice ─────────────────────────


def test_frozen_binding_translates_to_the_expression_resolver_already_emits(v4):
    """No reinterpretation: the frozen path IS what the resolver emits."""
    rb = resolve({})
    existing = rb.component_props["FilterPanel"]["filters"]
    out = apply_confirmed_bindings(rb, {"FilterPanel": _filter_binding()})
    assert out.component_props["FilterPanel"]["filters"] == existing
    assert out.component_props["FilterPanel"]["filters"] == JSVariable("_pageData.filters")


def test_translation_does_not_consult_the_registry(v4):
    """Registry binding for a different component cannot leak into FilterPanel."""
    rb = resolve({})
    out = apply_confirmed_bindings(rb, {"FilterPanel": _filter_binding()})
    assert out.component_props["FilterPanel"]["filters"].name == "_pageData.filters"
    assert "contract_params" not in out.provenance["FilterPanel"]["filters"]


def test_confirmed_provenance_replaces_slice_provenance(v4):
    rb = resolve({})
    assert rb.provenance["FilterPanel"]["filters"] == "slice:filters"
    out = apply_confirmed_bindings(rb, {"FilterPanel": _filter_binding()})
    assert out.provenance["FilterPanel"]["filters"] == "confirmed_binding:filters"


# ── it must never reinterpret ────────────────────────────────────────────


def test_non_page_data_path_refuses_instead_of_reinterpreting():
    b = DataBinding(
        source=DataSourceRef(kind="hook", ref="useDashboardData"),
        schema=None,
        mapping=(
            DataMappingEntry(prop="filters", from_field="contract_params['filters']", transform="identity"),
        ),
    )
    with pytest.raises(UntranslatableBinding) as e:
        translate_confirmed_binding(b, "FilterPanel")
    assert "architectural decision" in str(e.value)


def test_non_identity_transform_refuses_instead_of_reapplying():
    b = DataBinding(
        source=DataSourceRef(kind="hook", ref="useDashboardData"),
        schema=None,
        mapping=(DataMappingEntry(prop="data", from_field="_pageData.kpiData", transform="items"),),
    )
    with pytest.raises(UntranslatableBinding) as e:
        translate_confirmed_binding(b, "KpiRow")
    assert "reinterpret" in str(e.value)


def test_conflicting_frozen_entries_for_one_prop_refuse():
    b = DataBinding(
        source=DataSourceRef(kind="hook", ref="useDashboardData"),
        schema=None,
        mapping=(
            DataMappingEntry(prop="filters", from_field="_pageData.filters", transform="identity"),
            DataMappingEntry(prop="filters", from_field="_pageData.otros", transform="identity"),
        ),
    )
    with pytest.raises(UntranslatableBinding):
        translate_confirmed_binding(b, "FilterPanel")


# ── overlay scope ────────────────────────────────────────────────────────


def test_only_confirmed_components_are_overlaid(v4):
    rb = resolve({})
    out = apply_confirmed_bindings(rb, {"FilterPanel": _filter_binding()})
    for comp in ("KpiRow", "Timeseries", "BarChart", "AnalyticsTable", "Embed"):
        assert out.component_props[comp] == rb.component_props[comp]
        assert out.provenance[comp] == rb.provenance[comp]


def test_needs_choice_produces_no_overlay(v4, page_ds):
    """KpiRow/Timeseries stay needs_choice -> no confirmed binding -> untouched."""
    from app.intent.models import BindingRequirement

    for comp, prop in (("KpiRow", "data"), ("Timeseries", "data")):
        # Explicit requirement: with no contract these have no slot, so the
        # requirement is stated directly (same shape as the D tests).
        req = BindingRequirement(
            target_component=comp, required_props=(prop,),
            expected_shapes=(
                (prop, "array<KpiItem>") if comp == "KpiRow" else (prop, "array<Point>"),
            ),
        )
        d = discover_binding_candidates(
            req, v4_bindings=v4, page_data_source=page_ds,
            target_file_contents={"Page.tsx": _page()},
        )
        p = derive_binding_proposal(d, v4_bindings=v4)
        assert p.status == "needs_choice"
        assert p.mapping == ()

    rb = resolve({})
    out = apply_confirmed_bindings(rb, confirmed_bindings_from_actions([]))
    assert out == rb


def test_empty_confirmed_mapping_is_a_noop(v4):
    rb = resolve({})
    out = apply_confirmed_bindings(rb, {})
    assert out is rb


def test_apply_does_not_mutate_the_input(v4):
    rb = resolve({})
    before = {c: dict(p) for c, p in rb.component_props.items()}
    apply_confirmed_bindings(rb, {"FilterPanel": _filter_binding()})
    assert {c: dict(p) for c, p in rb.component_props.items()} == before


def test_preserves_page_data_source_and_imports(v4):
    rb = resolve({})
    out = apply_confirmed_bindings(rb, {"FilterPanel": _filter_binding()})
    assert out.page_data_source is rb.page_data_source
    assert out.imports == rb.imports
    assert out.consumed_params == rb.consumed_params


# ── extraction from confirmed actions ────────────────────────────────────


def test_confirmed_bindings_from_actions():
    a1 = IntentAction(verb="FilterPanel", target_capability="FilterPanel", binding=_filter_binding())
    a2 = IntentAction(verb="Embed", target_capability="Embed")
    got = confirmed_bindings_from_actions([a1, a2])
    assert set(got) == {"FilterPanel"}


def test_actions_without_binding_are_not_authority():
    assert confirmed_bindings_from_actions(
        [IntentAction(verb="Embed", target_capability="Embed")]
    ) == {}


# ── end to end: D proposal -> E confirmed -> physical ────────────────────


def test_filter_panel_chain_to_physical(v4, page_ds):
    """Real chain: contract+registry -> proposal(auto_unique) -> confirmed -> physical."""
    contract = _contract_for(
        "FilterPanel", {"filters": "filters"},
        input_schema={"properties": {"filters": {"type": "array", "items": "string"}}},
    )
    req = build_binding_requirement("FilterPanel", contract=contract, v4_bindings=v4)
    d = discover_binding_candidates(
        req, contract=contract, v4_bindings=v4, page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    p = derive_binding_proposal(d, v4_bindings=v4, contract=contract)
    assert p.status == "auto_unique"

    binding = DataBinding(
        source=p.source, schema=p.schema,
        mapping=tuple(
            DataMappingEntry(prop=e.prop, from_field=e.from_field, transform=e.transform)
            for e in p.mapping
        ),
    )
    assert [(e.prop, e.from_field) for e in binding.mapping] == [("filters", "_pageData.filters")]

    rb = resolve({})
    out = apply_confirmed_bindings(rb, {"FilterPanel": binding})
    assert out.component_props["FilterPanel"]["filters"] == JSVariable("_pageData.filters")
    assert out.provenance["FilterPanel"]["filters"].startswith("confirmed_binding")


def test_page_data_prefix_is_the_only_accepted_form():
    assert PAGE_DATA_PREFIX == "_pageData."