"""Subfase F — materializacion determinista del binding confirmado."""

from __future__ import annotations

import pytest

from app.binding.materialize import (
    CONFLICT_UNREPRESENTABLE_BINDING,
    UNREPRESENTABLE_BINDING_CAUSE,
    DriftItem,
    detect_registry_drift,
    materialize_confirmed_bindings,
    required_materialization,
)
from app.binding.resolver import resolve
from app.binding.translate import UntranslatableBinding
from app.graphir.backends.react_backend import JSVariable
from app.intent.models import DataBinding, DataMappingEntry, DataSchemaRef, DataSourceRef, IntentAction


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
    req = required_materialization(out)
    declaration, imports = req.declaration, list(req.imports)
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
    declaration, missing = required_materialization(out).declaration, required_materialization(out).missing
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
    item = drift.items[0]
    assert item.component == "FilterPanel" and item.prop == "filters"
    assert item.registry == "_pageData.filters"
    assert item.confirmed == "_pageData.otro"
    assert "registry resolves '_pageData.filters'" in item.describe()
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
    assert drift.items[0].registry is None
    assert "no physical value" in drift.items[0].describe()


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
    assert drift.items == ()  # structured, not just strings


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


def test_conflict_reuses_existing_post_confirm_envelope():
    """No invalid_confirmed_plan: el Plan sigue siendo valido."""
    assert CONFLICT_UNREPRESENTABLE_BINDING == "repository_conflict"
    assert CONFLICT_UNREPRESENTABLE_BINDING != "invalid_confirmed_plan"

# ── Cierre F: drift no bloqueante + binding no materializable ─────────────


def _conflict_of(pair, resolved=None):
    """pair = (component, binding)"""
    """Reproduce las dos ramas de CONFLICT que aplica apply_engine."""
    import copy

    from app.intent.models import conflict_result

    base = resolved if resolved is not None else resolve({})
    try:
        materialized, _drift = materialize_confirmed_bindings(
            base, _actions(pair)
        )
    except UntranslatableBinding as e:
        return conflict_result(
            CONFLICT_UNREPRESENTABLE_BINDING,
            f"NO WRITE: unrepresentable ({e})",
            details={"cause": UNREPRESENTABLE_BINDING_CAUSE, "detail": str(e)},
        )
    req = required_materialization(materialized)
    if req.missing:
        return conflict_result(
            CONFLICT_UNREPRESENTABLE_BINDING,
            "NO WRITE: " + "; ".join(req.missing),
            details={"cause": UNREPRESENTABLE_BINDING_CAUSE, "missing": list(req.missing)},
        )
    return None


def _assert_post_confirm_conflict(result):
    ex = result["execution"]
    assert ex["status"] == "conflict"
    assert ex["plan_confirmed"] is True
    assert ex["plan_retryable"] is True
    assert ex["run_phase"] == "confirmed"
    assert ex["conflict"] == "repository_conflict"
    assert ex["conflict"] != "invalid_confirmed_plan"
    assert ex["operations"] == []
    assert ex["diff"] is None


# --- drift no bloqueante ---

def test_drift_non_blocking_structured_diagnostic(rb):
    b = _binding(from_field="_pageData.otro")
    out, drift = materialize_confirmed_bindings(rb, _actions(("FilterPanel", b)))
    # detected
    assert drift.has_drift
    # structured
    payload = {"binding_drift": drift.to_dict()}
    assert payload["binding_drift"] == [
        {
            "component": "FilterPanel",
            "prop": "filters",
            "confirmed": "_pageData.otro",
            "registry": "_pageData.filters",
            "reason": "path_differs",
        }
    ]
    # materialization used the CONFIRMED binding
    assert out.component_props["FilterPanel"]["filters"] == JSVariable("_pageData.otro")
    # not a conflict
    assert _conflict_of(("FilterPanel", b)) is None


def test_drift_does_not_mutate_plan_or_registry(rb):
    import copy

    snapshot = copy.deepcopy(plan_snapshot := {"actions": [{"binding": "x"}]})
    before_props = {c: dict(p) for c, p in rb.component_props.items()}
    before_prov = {c: dict(p) for c, p in rb.provenance.items()}
    materialize_confirmed_bindings(rb, _actions(("FilterPanel", _binding("_pageData.otro"))))
    assert plan_snapshot == snapshot
    assert {c: dict(p) for c, p in rb.component_props.items()} == before_props
    assert {c: dict(p) for c, p in rb.provenance.items()} == before_prov


# --- drift sin falso positivo ---

def test_no_false_positive_when_confirmed_equals_registry(rb):
    out, drift = materialize_confirmed_bindings(rb, _actions(("FilterPanel", _binding())))
    assert drift.has_drift is False
    assert drift.to_dict() == []
    assert out.component_props["FilterPanel"]["filters"] == JSVariable("_pageData.filters")
    assert _conflict_of(("FilterPanel", _binding())) is None


def test_concrete_registry_value_is_structured_drift_never_conflict(rb):
    """F6.3 D3: valor concreto del registry -> DriftItem no bloqueante con
    preview de forma (JAMAS con el valor). Confirmed gana; no hay conflicto."""
    import json

    from app.binding.models import ResolvedBindings
    from app.binding.materialize import detect_registry_drift

    r = ResolvedBindings(component_props={"SearchBar": {"placeholder": "Search..."}})
    b = _binding("placeholder", "_pageData.q")
    drift = detect_registry_drift(r, {"SearchBar": b})
    assert drift.has_drift
    item = drift.items[0]
    assert item.component == "SearchBar"
    assert item.prop == "placeholder"
    assert item.reason == "registry_concrete_value"
    assert item.registry is None
    assert item.registry_value_shape == "string"
    assert "string" in item.describe()
    assert "Search..." not in json.dumps(drift.to_dict())

    out, drift2 = materialize_confirmed_bindings(r, _actions(("SearchBar", b)))
    assert drift2.items == drift.items
    # confirmed wins
    assert out.component_props["SearchBar"]["placeholder"] == JSVariable("_pageData.q")
    # never a conflict, never a fallback
    assert _conflict_of(("SearchBar", b)) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ([1, 2, 3], "list[3]"),
        ({"label": "x", "value": 1}, "dict{label, value}"),
        ({"a": 1, "b": 2, "c": 3, "d": 4}, "dict{a, b, c, ...}"),
        (42, "number"),
        (True, "boolean"),
        (JSVariable("_pageData.x"), "object"),
    ],
)
def test_concrete_value_shape_preview_never_leaks_the_value(value, expected):
    from app.binding.materialize import _shape_preview

    assert _shape_preview(value) == expected


# ── F6.3 D2-B: contradiccion de shape declarado (diagnostico, no conflicto) ─


def _binding_shape(prop, from_field, shape):
    return DataBinding(
        source=DataSourceRef(kind="hook", ref="useSalesData"),
        schema=DataSchemaRef(ref="contract:", shape=shape),
        mapping=(DataMappingEntry(prop=prop, from_field=from_field, transform="identity"),),
    )


def test_declared_shape_contradiction_is_structured_drift(rb):
    from app.binding.materialize import detect_shape_contradictions

    confirmed = {"KpiRow": _binding_shape("data", "_pageData.kpiData", "array<string>")}
    items = detect_shape_contradictions(confirmed, {"KpiRow": {"data": "object"}})
    assert len(items) == 1
    item = items[0]
    assert item.reason == "declared_shape_contradiction"
    assert item.component == "KpiRow" and item.prop == "data"
    assert item.confirmed == "array<string>"
    assert item.registry == "object"
    assert "array<string>" in item.describe() and "object" in item.describe()

    # no bloquea: la materializacion normal sigue y SOLO diagnostica
    out, drift = materialize_confirmed_bindings(
        rb, _actions(("KpiRow", _binding_shape("data", "_pageData.kpiData", "array<string>")))
    )
    assert "declared_shape_contradiction" not in [
        d.get("reason") for d in drift.to_dict()
    ]  # rb no declara shape: sin contradiccion en el camino unitario
    assert _conflict_of((
        "KpiRow", _binding_shape("data", "_pageData.kpiData", "array<string>"),
    )) is None


def test_shape_contradiction_both_directions_matter():
    from app.binding.materialize import detect_shape_contradictions

    confirmed = {"KpiRow": _binding_shape("data", "_pageData.kpiData", "object")}
    items = detect_shape_contradictions(confirmed, {"KpiRow": {"data": "array<KpiItem>"}})
    assert len(items) == 1
    assert items[0].reason == "declared_shape_contradiction"


def test_unknown_shape_never_contradicts():
    from app.binding.materialize import detect_shape_contradictions, _shape_family

    # confirmed unknown vs declared object -> no item
    confirmed = {"KpiRow": _binding_shape("data", "_pageData.kpiData", "unknown")}
    assert detect_shape_contradictions(confirmed, {"KpiRow": {"data": "object"}}) == ()
    # confirmed array vs declared unknown/missing -> no item
    confirmed = {"KpiRow": _binding_shape("data", "_pageData.kpiData", "array")}
    assert detect_shape_contradictions(confirmed, {"KpiRow": {"data": "unknown"}}) == ()
    assert detect_shape_contradictions(confirmed, {"KpiRow": {}}) == ()
    assert detect_shape_contradictions(confirmed, {}) == ()
    assert _shape_family(None) == "unknown"
    assert _shape_family("") == "unknown"
    assert _shape_family("any") == "unknown"


def test_known_shapes_of_the_same_family_do_not_contradict():
    from app.binding.materialize import detect_shape_contradictions

    confirmed = {"KpiRow": _binding_shape("data", "_pageData.kpiData", "array<string>")}
    assert detect_shape_contradictions(
        confirmed, {"KpiRow": {"data": "array<KpiItem>"}}
    ) == ()


def test_registry_declared_shapes_reads_the_ssot():
    from app.binding.materialize import registry_declared_shapes

    declared = registry_declared_shapes()
    assert declared.get("KpiRow", {}).get("data") == "array<KpiItem>"
    assert declared.get("Timeseries", {}).get("title") == "string"
    # componentes sin declaracion de shape (slices de composicion) no aparecen
    assert "FilterPanel" not in declared


# --- binding no materializable: las 4 causas ---

def test_untranslatable_from_field_is_post_confirm_conflict(rb):
    r = _conflict_of(("FilterPanel", _binding(from_field="contract_params['filters']")))
    _assert_post_confirm_conflict(r)


def test_missing_page_data_source_is_post_confirm_conflict(rb):
    import copy

    no_page = copy.copy(rb)
    no_page.page_data_source = None
    r = _conflict_of(("FilterPanel", _binding()), resolved=no_page)
    _assert_post_confirm_conflict(r)


def test_unrepresentable_transform_is_post_confirm_conflict(rb):
    r = _conflict_of(("KpiRow", _binding("data", "_pageData.kpiData", "items")))
    _assert_post_confirm_conflict(r)


def test_physical_conflict_between_confirmed_bindings(rb):
    """Two confirmed bindings claiming one prop differently."""
    import copy

    from app.intent.models import conflict_result

    dup = DataBinding(
        source=DataSourceRef(kind="hook", ref="useDashboardData"),
        schema=None,
        mapping=(
            DataMappingEntry(prop="filters", from_field="_pageData.filters"),
            DataMappingEntry(prop="filters", from_field="_pageData.otro"),
        ),
    )
    base = copy.copy(rb)
    try:
        materialize_confirmed_bindings(base, _actions(("FilterPanel", dup)))
        raise AssertionError("expected UntranslatableBinding")
    except UntranslatableBinding:
        pass
    _assert_post_confirm_conflict(
        conflict_result(
            CONFLICT_UNREPRESENTABLE_BINDING,
            "NO WRITE: conflicting frozen entries",
            details={"cause": UNREPRESENTABLE_BINDING_CAUSE},
        )
    )


@pytest.mark.parametrize(
    "binding",
    [
        _binding(from_field="contract_params['filters']"),
        _binding("data", "_pageData.kpiData", "items"),
    ],
)
def test_no_fallback_no_registry_substitution_on_failure(rb, binding):
    """Untranslatable must not silently fall back to the registry value."""
    before = rb.component_props["FilterPanel"]["filters"]
    with pytest.raises(UntranslatableBinding):
        materialize_confirmed_bindings(rb, _actions(("FilterPanel", binding)))
    # registry value intact: nothing substituted it
    assert rb.component_props["FilterPanel"]["filters"] == before


# --- idempotencia ---

def test_retry_same_result_is_idempotent(rb):
    actions = _actions(("FilterPanel", _binding()))
    a1, d1 = materialize_confirmed_bindings(rb, actions)
    a2, d2 = materialize_confirmed_bindings(a1, actions)
    assert a1.component_props == a2.component_props
    assert a1.provenance == a2.provenance
    assert d1.to_dict() == d2.to_dict()
    assert d1.items == () and d2.items == ()  # sin drift no hay items


def test_retry_does_not_create_alternative_bindings(rb):
    b = _binding(from_field="_pageData.otro")
    a1, _ = materialize_confirmed_bindings(rb, _actions(("FilterPanel", b)))
    a2, _ = materialize_confirmed_bindings(a1, _actions(("FilterPanel", b)))
    assert list(a2.component_props["FilterPanel"]) == ["filters"]
    assert a2.component_props["FilterPanel"]["filters"] == JSVariable("_pageData.otro")


def test_retry_does_not_change_conflict_shape(rb):
    b = _binding(from_field="contract_params['filters']")
    r1 = _conflict_of(("FilterPanel", b))
    r2 = _conflict_of(("FilterPanel", b))
    assert r1["execution"] == r2["execution"]


def test_no_physical_representation_is_post_confirm_conflict():
    """resolved=None: antes se saltaba en silencio. Ahora es CONFLICT retryable."""
    req = required_materialization(None, {"FilterPanel": _binding()})
    assert req.missing
    assert "no physical representation" in req.missing[0]
    assert req.imports == () and req.declaration is None


def test_no_physical_representation_not_required_when_no_page_data():
    req = required_materialization(None, {"FilterPanel": _binding("q", "text")})
    assert req.missing == ()


def test_structured_diagnostic_payload_is_serializable():
    _, drift = materialize_confirmed_bindings(
        resolve({}), _actions(("FilterPanel", _binding("_pageData.otro")))
    )
    import json

    json.dumps({"diagnostics": {"binding_drift": drift.to_dict()}})  # no raise
