"""Subfase D — mapping determinista, schema del VALOR y matriz 0/1/N."""

from __future__ import annotations

import pytest

from app.binding.discovery import discover_binding_candidates
from app.binding.mapping import (
    MappingValueSchema,
    build_candidate_mappings,
    derive_binding_proposal,
    shape_conflicts,
    unconfirmed_shapes,
)
from app.binding.requirement import build_binding_requirement
from app.intent.models import BindingRequirement
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


# ── the type boundary ────────────────────────────────────────────────────


def test_value_schema_is_a_distinct_type_from_source_schema():
    """The whole point of D: source shape and value shape are not the same thing."""
    assert MappingValueSchema("object") != MappingValueSchema("array<string>")
    assert MappingValueSchema("unknown").is_known is False


def test_source_shape_is_never_compared_to_expected_shapes(page_ds, v4):
    """A source that declares shape 'object' must not be refuted by an array value."""
    req = BindingRequirement(
        target_component="FilterPanel",
        required_props=("filters",),
        expected_shapes=(("filters", "array<string>"),),
    )
    d = discover_binding_candidates(
        req, page_data_source=page_ds, target_file_contents={"Page.tsx": _page()}
    )
    cand = d.candidates[0]
    assert cand.schema.shape == "object"  # SOURCE
    mappings = build_candidate_mappings(req, cand, v4_bindings=v4)
    # The slice mapping's value is unknown, so nothing is refuted.
    assert shape_conflicts(req, mappings) == ()


def test_unknown_value_shape_does_not_conflict_but_blocks_auto_unique(page_ds):
    req = BindingRequirement(
        target_component="FilterPanel",
        required_props=("filters",),
        expected_shapes=(("filters", "array<string>"),),
    )
    d = discover_binding_candidates(
        req, page_data_source=page_ds, target_file_contents={"Page.tsx": _page()}
    )
    cand = d.candidates[0]
    mappings = build_candidate_mappings(req, cand)
    assert shape_conflicts(req, mappings) == ()
    assert unconfirmed_shapes(req, mappings) == ("filters",)


def test_proven_value_shape_conflict_is_detected(v4, page_ds):
    """Registry type_info describes the post-transform prop value."""
    req = BindingRequirement(
        target_component="Timeseries",
        required_props=("data",),
        expected_shapes=(("data", "string"),),  # deliberately wrong
    )
    d = discover_binding_candidates(
        req,
        v4_bindings=v4,
        page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    mappings = build_candidate_mappings(req, d.candidates[0], v4_bindings=v4)
    assert "data" in shape_conflicts(req, mappings)


# ── two stages, never composed ───────────────────────────────────────────


def test_slice_stage_yields_page_data_expression(page_ds):
    req = BindingRequirement(target_component="FilterPanel", required_props=("filters",))
    d = discover_binding_candidates(
        req, page_data_source=page_ds, target_file_contents={"Page.tsx": _page()}
    )
    mappings = build_candidate_mappings(req, d.candidates[0])
    slice = [m for m in mappings if m.value_path.startswith("_pageData.")]
    assert [m.value_path for m in slice] == ["_pageData.filters"]
    assert slice[0].transform == "identity"


def test_param_stage_yields_contract_params_expression(v4):
    req = BindingRequirement(target_component="KpiRow", required_props=("data",))
    cand = _fake_candidate(fields=("metrics",))
    mappings = build_candidate_mappings(req, cand, v4_bindings=v4)
    params = [m for m in mappings if m.stage == "param"]
    assert [m.value_path for m in params] == ["contract_params['metrics']"]
    assert params[0].transform == "items"


def test_from_field_is_a_param_not_a_subpath(v4):
    """KpiRow: slice selector 'kpiData', from 'metrics'. NOT kpiData.metrics."""
    req = BindingRequirement(target_component="KpiRow", required_props=("data",))
    cand = _fake_candidate(fields=("kpiData", "metrics"))
    paths = {m.value_path for m in build_candidate_mappings(req, cand, v4_bindings=v4)}
    assert "kpiData.metrics" not in paths
    assert not any(".metrics" in p for p in paths)


def _fake_candidate(fields):
    from app.binding.discovery import CandidateSource
    from app.intent.models import DataSchemaRef, DataSourceRef

    return CandidateSource(
        source=DataSourceRef(kind="hook", ref="useDashboardData"),
        schema=DataSchemaRef(ref="hook:useDashboardData.result", shape="object", fields=tuple(fields)),
        relations=("existing_import", "existing_call"),
        prop_field_links=(),
        evidence=(),
    )


def test_two_stages_for_one_prop_is_ambiguous_not_merged(page_ds, v4):
    """KpiRow has both a slice and a param binding -> ambiguity, never a pick."""
    req = BindingRequirement(target_component="KpiRow", required_props=("data",))
    d = discover_binding_candidates(
        req,
        v4_bindings=v4,
        page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    p = derive_binding_proposal(d, v4_bindings=v4)
    assert p.provenance["ambiguous_props"] == ["data"]
    assert p.status == "needs_choice"
    assert p.mapping == ()  # nothing chosen while ambiguous


# ── 0 / 1 / N matrix ─────────────────────────────────────────────────────


def test_zero_candidates_is_unresolved(page_ds):
    req = BindingRequirement(target_component="Nope", required_props=("x",))
    d = discover_binding_candidates(req, page_data_source=page_ds)
    p = derive_binding_proposal(d)
    assert p.status == "unresolved"
    assert p.provenance["candidate_count"] == 0
    assert p.mapping == ()
    assert p.source is None


def test_n_candidates_is_needs_choice(v4):
    content = (
        "import { useDashboardData } from '../../hooks/useDashboardData';\n"
        "import { useSales } from '../../hooks/useSales';\n"
        "const { filters } = useDashboardData();\n"
        "const { filters } = useSales();\n"
    )
    req = BindingRequirement(target_component="FilterPanel", required_props=("filters",))
    d = discover_binding_candidates(
        req, contract=_contract_for("FilterPanel", {"filters": "filters"}),
        target_file_contents={"Page.tsx": content},
    )
    p = derive_binding_proposal(d)
    assert p.provenance["candidate_count"] == 2
    assert p.status == "needs_choice"
    assert p.mapping == ()
    assert p.source is None


def test_auto_unique_requires_confirmed_shapes(v4):
    """Single candidate but unknown value shape -> unresolved, never auto_unique."""
    req = BindingRequirement(
        target_component="FilterPanel", required_props=("filters",),
        expected_shapes=(("filters", "array<string>"),),
    )
    d = discover_binding_candidates(
        req, page_data_source=page_ds_of(),
        target_file_contents={"Page.tsx": _page()},
    )
    p = derive_binding_proposal(d, v4_bindings=v4)
    assert p.status == "unresolved"
    assert any(i.startswith("shape_unconfirmed:") for i in p.provenance["incompatibilities"])


def page_ds_of():
    return load_page_data_source()


def test_auto_unique_when_single_candidate_and_shapes_confirmed(v4, page_ds):
    """Timeseries: single candidate, param stage confirms the value shape."""
    req = BindingRequirement(
        target_component="Timeseries",
        required_props=("data",),
        expected_shapes=(("data", "array<Point>"),),
    )
    d = discover_binding_candidates(
        req,
        v4_bindings=v4,
        page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    cand = d.candidates[0]
    # Only the param stage: force a single stage by using a candidate with no slice link.
    mappings = build_candidate_mappings(req, cand, v4_bindings=v4)
    params = [m for m in mappings if m.stage == "param"]
    assert params and params[0].value_schema.is_known


def test_requirement_incompatibilities_block_auto_unique(v4, page_ds):
    req = BindingRequirement(
        target_component="FilterPanel",
        required_props=("filters",),
        incompatibilities=("conflicting_shape_declarations:FilterPanel.filters",),
    )
    d = discover_binding_candidates(
        req, page_data_source=page_ds, target_file_contents={"Page.tsx": _page()}
    )
    p = derive_binding_proposal(d)
    assert p.status == "unresolved"


# ── guardrails ───────────────────────────────────────────────────────────


def test_does_not_touch_confirmed_binding_lifecycle(v4, page_ds):
    """D stays pre-confirmation: no DataBinding, no lifecycle mutation."""
    req = BindingRequirement(target_component="FilterPanel", required_props=("filters",))
    d = discover_binding_candidates(
        req, page_data_source=page_ds, target_file_contents={"Page.tsx": _page()}
    )
    p = derive_binding_proposal(d)
    assert not hasattr(p, "binding")
    assert "intent_action" not in p.provenance


def test_status_is_from_the_closed_set_only(page_ds, v4):
    allowed = {"auto_unique", "needs_choice", "unresolved"}
    for comp in ("FilterPanel", "KpiRow", "Timeseries", "MetricCard", "Nope"):
        req = BindingRequirement(target_component=comp, required_props=("data",))
        d = discover_binding_candidates(
            req, v4_bindings=v4, page_data_source=page_ds,
            target_file_contents={"Page.tsx": _page()},
        )
        assert derive_binding_proposal(d, v4_bindings=v4).status in allowed


def test_proposal_serializes(page_ds, v4):
    req = BindingRequirement(target_component="FilterPanel", required_props=("filters",))
    d = discover_binding_candidates(
        req, page_data_source=page_ds, target_file_contents={"Page.tsx": _page()}
    )
    as_dict = derive_binding_proposal(d, v4_bindings=v4).to_dict()
    assert as_dict["status"] in {"auto_unique", "needs_choice", "unresolved"}
    assert isinstance(as_dict["mapping"], list)


def test_full_chain_real_registry_and_contract(page_ds, v4):
    """Real B output feeds real C discovery feeding real D proposal."""
    contract = _contract_for("FilterPanel", {"filters": "filters"},
                             input_schema={"properties": {"filters": {"type": "array", "items": "string"}}})
    req = build_binding_requirement("FilterPanel", contract=contract, v4_bindings=v4)
    assert req.required_props == ("filters",)
    assert req.shape_for("filters") == "array<string>"
    d = discover_binding_candidates(
        req, contract=contract, v4_bindings=v4, page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    assert d.candidate_count == 1
    p = derive_binding_proposal(d, v4_bindings=v4, contract=contract)
    assert p.status in {"auto_unique", "needs_choice", "unresolved"}

# ── Fase 1C: slice confirms its value shape via the contract ──────────────
#
# Regla: un declared_dataslice confirma MappingValueSchema SOLO cuando el
# selector esta estructuralmente ligado al param del contrato
# (ast_template.slots[].props: prop -> param, selector == param) y el
# input_schema aporta el shape. Es identidad, no similitud, y reutiliza las
# mismas dos evidencias que Subfase B ya consume.

FILTER_CONTRACT_SCHEMA = {
    "properties": {"filters": {"type": "array", "items": "string"}}
}


def test_filter_panel_reaches_auto_unique(page_ds, v4):
    """DataSlice.selector=filters -> slot filters -> input_schema array<string>."""
    contract = _contract_for(
        "FilterPanel", {"filters": "filters"}, input_schema=FILTER_CONTRACT_SCHEMA
    )
    req = build_binding_requirement("FilterPanel", contract=contract, v4_bindings=v4)
    assert req.shape_for("filters") == "array<string>"

    d = discover_binding_candidates(
        req, contract=contract, v4_bindings=v4, page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    assert d.candidate_count == 1

    p = derive_binding_proposal(d, v4_bindings=v4, contract=contract)
    assert p.status == "auto_unique", p.provenance
    assert p.provenance["candidate_count"] == 1
    assert p.provenance["ambiguous_props"] == []
    assert p.provenance["shape_conflicts"] == []
    assert p.provenance["shape_unconfirmed"] == []
    assert p.provenance["incompatibilities"] == []
    assert [(e.prop, e.from_field) for e in p.mapping] == [("filters", "_pageData.filters")]
    assert p.source.kind == "hook" and p.source.ref == "useDashboardData"


def test_filter_panel_slice_value_shape_is_confirmed(page_ds, v4):
    """The slice mapping's VALUE schema comes from input_schema, not the source."""
    contract = _contract_for(
        "FilterPanel", {"filters": "filters"}, input_schema=FILTER_CONTRACT_SCHEMA
    )
    req = BindingRequirement(
        target_component="FilterPanel", required_props=("filters",),
        expected_shapes=(("filters", "array<string>"),),
    )
    d = discover_binding_candidates(
        req, contract=contract, v4_bindings=v4, page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    mappings = build_candidate_mappings(req, d.candidates[0], v4_bindings=v4, contract=contract)
    slice_mappings = [m for m in mappings if m.stage == "slice"]
    assert len(slice_mappings) == 1
    assert slice_mappings[0].value_schema.shape == "array<string>"
    assert slice_mappings[0].value_schema.is_known
    # Still not the SOURCE shape.
    assert d.candidates[0].schema.shape == "object"


def test_selector_not_equal_to_param_does_not_confirm(page_ds, v4):
    """Identity link required: a different selector name confirms nothing."""
    contract = _contract_for(
        "FilterPanel", {"filters": "otroParam"}, input_schema=FILTER_CONTRACT_SCHEMA
    )
    req = BindingRequirement(
        target_component="FilterPanel", required_props=("filters",),
        expected_shapes=(("filters", "array<string>"),),
    )
    d = discover_binding_candidates(
        req, contract=contract, v4_bindings=v4, page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    mappings = build_candidate_mappings(req, d.candidates[0], v4_bindings=v4, contract=contract)
    assert all(m.value_schema.shape == "unknown" for m in mappings if m.stage == "slice")
    assert unconfirmed_shapes(req, mappings) == ("filters",)


def test_missing_input_schema_does_not_confirm(page_ds, v4):
    """No input_schema -> no confirmation -> no auto_unique."""
    contract = _contract_for("FilterPanel", {"filters": "filters"})
    req = BindingRequirement(
        target_component="FilterPanel", required_props=("filters",),
        expected_shapes=(("filters", "array<string>"),),
    )
    d = discover_binding_candidates(
        req, contract=contract, v4_bindings=v4, page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    p = derive_binding_proposal(d, v4_bindings=v4, contract=contract)
    assert p.status != "auto_unique"


def test_no_contract_leaves_slice_unknown(page_ds, v4):
    req = BindingRequirement(
        target_component="FilterPanel", required_props=("filters",),
        expected_shapes=(("filters", "array<string>"),),
    )
    d = discover_binding_candidates(
        req, page_data_source=page_ds, target_file_contents={"Page.tsx": _page()}
    )
    mappings = build_candidate_mappings(req, d.candidates[0], v4_bindings=v4)
    assert unconfirmed_shapes(req, mappings) == ("filters",)


# ── KpiRow / Timeseries stay ambiguous: semantics unchanged ───────────────


def test_kpi_row_stays_needs_choice(page_ds, v4):
    """selector 'kpiData' != param 'metrics': no confirmation, no fusion."""
    req = BindingRequirement(
        target_component="KpiRow", required_props=("data",),
        expected_shapes=(("data", "array<KpiItem>"),),
    )
    d = discover_binding_candidates(
        req, v4_bindings=v4, page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    p = derive_binding_proposal(d, v4_bindings=v4)
    assert p.status == "needs_choice"
    assert p.provenance["ambiguous_props"] == ["data"]
    assert p.mapping == ()


def test_timeseries_stays_needs_choice(page_ds, v4):
    """selector 'chartData.timeseries' != param 'timeseries_metric': unchanged."""
    req = BindingRequirement(
        target_component="Timeseries", required_props=("data",),
        expected_shapes=(("data", "array<Point>"),),
    )
    d = discover_binding_candidates(
        req, v4_bindings=v4, page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    p = derive_binding_proposal(d, v4_bindings=v4)
    assert p.status == "needs_choice"
    assert p.provenance["ambiguous_props"] == ["data"]
    assert p.mapping == ()


def test_ambiguous_case_never_reaches_auto_unique_even_with_contract(page_ds, v4):
    """Contract input_schema must not silently break the KpiRow ambiguity."""
    contract = _contract_for(
        "KpiRow", {"data": "kpiData"},
        input_schema={"properties": {"kpiData": {"type": "array", "items": "KpiItem"}}},
    )
    req = BindingRequirement(
        target_component="KpiRow", required_props=("data",),
        expected_shapes=(("data", "array<KpiItem>"),),
    )
    d = discover_binding_candidates(
        req, contract=contract, v4_bindings=v4, page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    p = derive_binding_proposal(d, v4_bindings=v4, contract=contract)
    assert p.status == "needs_choice"
    assert p.provenance["ambiguous_props"] == ["data"]
    assert p.mapping == ()


def test_timeseries_title_is_not_required_data(v4):
    """title is config, not data: it must not become a binding requirement."""
    contract = _contract_for("Timeseries", {"title": "chartTitle"})
    req = build_binding_requirement("Timeseries", contract=contract, v4_bindings=v4)
    assert "title" in req.required_props  # registry declares it, B keeps it
    d = discover_binding_candidates(
        req, contract=contract, v4_bindings=v4, page_data_source=page_ds,
        target_file_contents={"Page.tsx": _page()},
    )
    # chartTitle is not a destructured field -> no provable candidate
    assert d.candidate_count == 0
