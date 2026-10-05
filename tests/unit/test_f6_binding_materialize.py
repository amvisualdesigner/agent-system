"""Subfase F — materializacion determinista del binding confirmado."""

from __future__ import annotations

import pytest

from app.binding.materialize import (
    CONFLICT_UNREPRESENTABLE_BINDING,
    detect_registry_drift,
    materialize_confirmed_bindings,
    required_materialization,
)
from app.binding.resolver import resolve
from app.binding.translate import UntranslatableBinding
from app.graphir.backends.react_backend import JSVariable
from app.intent.models import DataBinding, DataMappingEntry, DataSourceRef, IntentAction


def _binding(prop="filters", from_field="_pageData.filters", transform="identity"):
    return DataBinding(
        source=DataSourceRef(kind="hook", ref="useDashboardData"),
        schema=None,
        mapping=(DataMappingEntry(prop=prop, from_field=from_field, transform=transform),),
    )


def _actions(*bindings):
    return [
        IntentAction(verb=c, target_capability=c, binding=b)
        for c, b in bindings
    ]


@pytest.fixture(scope="module")
def rb():
    return resolve({})


# ── injection point and materialization ──────────────────────────────────


def test_materialization_preserves_confirmed_semantic_authority(rb):
    out, drift = materialize_confirmed_bindings(
        rb, _actions(("FilterPanel", _binding()))
    )
    assert out.component_props["FilterPanel"]["filters"] == JSVariable("_pageData.filters")
    assert out.provenance["FilterPanel"]["filters"] == "confirmed_binding:filters"


def test_materialization_is_idempotent(rb):
    once, _ = materialize_confirmed_bindings(rb, _actions(("FilterPanel", _binding())))
    twice, _ = materialize_confirmed_bindings(once, _actions(("FilterPanel", _binding())))
    assert once.component_props == twice.component_props
    assert once.provenance == twice.provenance


def test_no_confirmed_actions_is_a_noop(rb):
    out, drift = materialize_confirmed_bindings(rb, [])
    assert out is rb
    assert drift.has_drift is False


def test_none_resolved_stays_none():
    out, drift = materialize_confirmed_bindings(None, _actions(("FilterPanel", _binding())))
    assert out is None
    assert drift.has_drift is False


def test_only_confirmed_components_change(rb):
    out, _ = materialize_confirmed_bindings(rb, _actions(("FilterPanel", _binding())))
    for comp in ("KpiRow", "Timeseries", "BarChart", "AnalyticsTable", "Embed", "Form"):
        assert out.component_props[comp] == rb.component_props[comp]
        assert out.provenance[comp] == rb.provenance[comp]


def test_page_data_source_and_imports_preserved(rb):
    out, _ = materialize_confirmed_bindings(rb, _actions(("FilterPanel", _binding())))
    assert out.page_data_source is rb.page_data_source
    assert out.imports == rb.imports
    assert out.consumed_params == rb.consumed_params


# ── hook declaration + imports from the confirmed binding ────────────────


def test_hook_declaration_and_import_are_derived(rb):
    out, _ = materialize_confirmed_bindings(rb, _actions(("FilterPanel", _binding())))
    declaration, imports = required_materialization(out)
    assert declaration == "const _pageData = useDashboardData();"
    assert imports == ["import { useDashboardData } from '@/hooks/useDashboardData'"]


def test_missing_page_data_source_is_unmaterializable(rb):
    """Confirmed binding demands _pageData but physics cannot generate it."""
    import copy

    no_page = copy.copy(rb)
    no_page.page_data_source = None
    out, _ = materialize_confirmed_bindings(
        no_page, _actions(("FilterPanel", _binding()))
    )
    declaration, missing = required_materialization(out)
    assert declaration is None
    assert missing and "_pageData" in missing[0]


def test_untranslatable_binding_raises_not_falls_back(rb):
    b = _binding(from_field="contract_params['filters']")
    with pytest.raises(UntranslatableBinding):
        materialize_confirmed_bindings(rb, _actions(("FilterPanel", b)))
    # And the input was not mutated by the failed attempt.
    assert rb.provenance["FilterPanel"]["filters"] == "slice:filters"


def test_non_identity_transform_raises(rb):
    b = _binding(prop="data", from_field="_pageData.kpiData", transform="items")
    with pytest.raises(UntranslatableBinding):
        materialize_confirmed_bindings(rb, _actions(("KpiRow", b)))


# ── drift: registry is evidence, confirmed binding wins ──────────────────


def test_drift_is_reported_and_confirmed_binding_still_wins(rb):
    b = _binding(from_field="_pageData.otro")
    out, drift = materialize_confirmed_bindings(rb, _actions(("FilterPanel", b)))
    assert drift.has_drift
    assert "registry resolves '_pageData.filters'" in drift.items[0]
    # Registry did NOT get to keep its value.
    assert out.component_props["FilterPanel"]["filters"] == JSVariable("_pageData.otro")


def test_no_drift_when_registry_agrees(rb):
    _, drift = materialize_confirmed_bindings(
        rb, _actions(("FilterPanel", _binding()))
    )
    assert drift.has_drift is False


def test_drift_when_registry_has_no_physical_value():
    from app.binding.models import ResolvedBindings

    empty = ResolvedBindings()
    drift = detect_registry_drift(empty, {"FilterPanel": _binding()})
    assert drift.has_drift
    assert "no physical value" in drift.items[0]


def test_drift_detection_is_pure(rb):
    before = {c: dict(p) for c, p in rb.provenance.items()}
    detect_registry_drift(rb, {"FilterPanel": _binding(from_field="_pageData.otro")})
    assert {c: dict(p) for c, p in rb.provenance.items()} == before


# ── KpiRow / Timeseries stay unresolved in F ─────────────────────────────


def test_kpi_row_and_timeseries_not_resolved_by_f(rb):
    """No confirmed binding -> registry resolution stands. F does not decide."""
    out, drift = materialize_confirmed_bindings(
        rb, _actions(("FilterPanel", _binding()))
    )
    assert out.component_props["KpiRow"]["data"] == JSVariable("_pageData.kpiData")
    assert out.component_props["Timeseries"]["data"] == JSVariable(
        "_pageData.chartData.timeseries"
    )
    assert out.provenance["KpiRow"]["data"].startswith("slice:")
    assert drift.items == ()


# ── apply_engine integration ─────────────────────────────────────────────


def test_apply_engine_reads_bindings_from_confirmed_plan():
    from app.engine.apply_engine import _confirmed_binding_actions

    binding = {
        "source": {"kind": "hook", "ref": "useDashboardData"},
        "mapping": [{"prop": "filters", "from_field": "_pageData.filters"}],
    }
    got = _confirmed_binding_actions(
        {"actions": [{"verb": "FilterPanel", "target_capability": "FilterPanel", "binding": binding}]}
    )
    assert [(a.target_capability, a.binding.mapping[0].from_field) for a in got] == [
        ("FilterPanel", "_pageData.filters")
    ]


def test_apply_engine_ignores_actions_without_binding():
    from app.engine.apply_engine import _confirmed_binding_actions

    assert _confirmed_binding_actions({"actions": [{"verb": "Embed", "target_capability": "Embed"}]}) == []


def test_apply_engine_ignores_incomplete_binding():
    from app.engine.apply_engine import _confirmed_binding_actions

    assert _confirmed_binding_actions(
        {"actions": [{"verb": "X", "binding": {"mapping": []}}]}
    ) == []


def test_conflict_type_is_registered():
    assert CONFLICT_UNREPRESENTABLE_BINDING == "invalid_confirmed_plan"