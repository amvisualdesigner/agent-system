"""Fase 6 · Subfase A — Modelo tipado de Data Binding + transporte zero-loss.

El binding confirmado es un CONTRATO TIPADO (no dict libre) y viaja sin
pérdida: IntentAction → ConfirmedIntent → CompiledPlan → semantic_frame.

Invariantes cubiertas aquí:
  - DataBinding exige source.ref no vacío y ≥1 mapping entry.
  - round-trip to_dict/from_dict estable.
  - ConfirmedIntent preserva el binding confirmado.
  - PlanCompiler copia el binding a actions[] y semantic_frame.actions[].
  - binding ausente (None) NO se materializa en el plan (compatibilidad
    con planes existentes donde no hay binding).
  - BindingProposal NO es DataBinding; to_binding() solo para estados
    resueltos.
  - EvidenceItem no es autoridad (solo metadata).
"""

from dataclasses import asdict

import pytest

from app.intent.models import (
    BindingProposal,
    BindingRequirement,
    DataBinding,
    DataMappingEntry,
    DataSchemaRef,
    DataSourceRef,
    EvidenceItem,
    IntentAction,
    ConfirmedIntent,
)
from app.intent.plan_compiler import compile_plan


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _slice_binding() -> DataBinding:
    return DataBinding(
        source=DataSourceRef(
            kind="slice",
            ref="slice:SalesOverviewPage.filters",
            selector="filters",
        ),
        schema=DataSchemaRef(
            ref="data_access:Page.slices",
            shape="array",
            fields=("filters",),
        ),
        mapping=(
            DataMappingEntry(
                prop="filters",
                from_field="filters",
                transform="identity",
                required=False,
                default=[],
            ),
        ),
    )


def _hook_binding() -> DataBinding:
    return DataBinding(
        source=DataSourceRef(kind="hook", ref="hook:useDashboardData", selector="kpiData"),
        schema=DataSchemaRef(ref="data_access:Page.slices", shape="array", fields=("kpiData",)),
        mapping=(
            DataMappingEntry(prop="data", from_field="kpiData", transform="items", required=True),
        ),
    )


# ═══════════════════════════════════════════════════════════════════
# A.1 — DataSourceRef
# ═══════════════════════════════════════════════════════════════════


class TestDataSourceRef:
    def test_identity_is_kind_ref_selector(self):
        s = DataSourceRef(kind="hook", ref="hook:useDashboardData", selector="kpiData")
        assert s.identity() == ("hook", "hook:useDashboardData", "kpiData")

    def test_roundtrip_preserves_selector(self):
        s = DataSourceRef(kind="slice", ref="slice:Page.filters", selector="filters")
        assert DataSourceRef.from_dict(s.to_dict()) == s

    def test_selector_omitted_when_none(self):
        s = DataSourceRef(kind="service", ref="service:api/sales.getSales")
        assert "selector" not in s.to_dict()
        assert DataSourceRef.from_dict(s.to_dict()) == s

    def test_from_dict_none_returns_none(self):
        assert DataSourceRef.from_dict(None) is None
        assert DataSourceRef.from_dict({}) is None


# ═══════════════════════════════════════════════════════════════════
# A.2 — DataSchemaRef (referencia, NO snapshot de tipos)
# ═══════════════════════════════════════════════════════════════════


class TestDataSchemaRef:
    def test_defaults_to_unknown_shape(self):
        s = DataSchemaRef(ref="x")
        assert s.shape == "unknown"
        assert s.fields == ()

    def test_roundtrip(self):
        s = DataSchemaRef(ref="data_access:Page.slices", shape="array", fields=("filters", "regions"))
        assert DataSchemaRef.from_dict(s.to_dict()) == s

    def test_fields_accepts_bare_string(self):
        assert DataSchemaRef.from_dict({"ref": "r", "fields": "only"}).fields == ("only",)


# ═══════════════════════════════════════════════════════════════════
# A.3 — DataMappingEntry
# ═══════════════════════════════════════════════════════════════════


class TestDataMappingEntry:
    def test_roundtrip_all_fields(self):
        e = DataMappingEntry(prop="p", from_field="f", transform="items", required=True, default=[1])
        assert DataMappingEntry.from_dict(e.to_dict()) == e

    def test_optional_fields_omitted_when_unset(self):
        d = DataMappingEntry(prop="p", from_field="f").to_dict()
        assert d == {"prop": "p", "from_field": "f", "required": False}

    def test_required_defaults_false(self):
        assert DataMappingEntry(prop="p", from_field="f").required is False


# ═══════════════════════════════════════════════════════════════════
# A.4 — DataBinding (contrato congelado)
# ═══════════════════════════════════════════════════════════════════


class TestDataBinding:
    def test_rejects_empty_source_ref(self):
        with pytest.raises(ValueError, match="non-empty source.ref"):
            DataBinding(source=DataSourceRef(kind="hook", ref=""), mapping=(_m(),))

    def test_rejects_empty_mapping(self):
        with pytest.raises(ValueError, match="at least one mapping entry"):
            DataBinding(source=DataSourceRef(kind="hook", ref="hook:x"), mapping=())

    def test_is_frozen(self):
        b = _slice_binding()
        with pytest.raises(Exception):
            b.mapping = ()

    def test_roundtrip_preserves_equality(self):
        b = _slice_binding()
        assert DataBinding.from_dict(b.to_dict()) == b

    def test_schema_is_optional(self):
        b = DataBinding(
            source=DataSourceRef(kind="hook", ref="hook:useDashboardData"),
            mapping=(DataMappingEntry(prop="data", from_field="data"),),
        )
        rt = DataBinding.from_dict(b.to_dict())
        assert rt.schema is None
        assert rt == b

    def test_mapping_for_lookup(self):
        b = _slice_binding()
        assert b.mapping_for("filters") is not None
        assert b.mapping_for("nope") is None

    def test_from_dict_none_and_empty(self):
        assert DataBinding.from_dict(None) is None
        assert DataBinding.from_dict({}) is None

    def test_from_dict_incomplete_yields_none(self):
        """Dict incompleto = ausencia de binding, no excepción.

        La decisión 'esta acción requería binding y no lo tiene' se toma en
        la validación de confirm (invalid_confirmed_plan), no en el transporte.
        """
        assert DataBinding.from_dict({"source": {"kind": "hook", "ref": "hook:x"}}) is None
        assert DataBinding.from_dict({"source": {"kind": "hook", "ref": ""}, "mapping": [{}]}) is None
        assert DataBinding.from_dict({"mapping": [{"prop": "p", "from_field": "f"}]}) is None


def _m() -> DataMappingEntry:
    return DataMappingEntry(prop="p", from_field="f")


# ═══════════════════════════════════════════════════════════════════
# A.5 — BindingRequirement (rompe la circularidad)
# ═══════════════════════════════════════════════════════════════════


class TestBindingRequirement:
    def test_shape_lookup(self):
        r = BindingRequirement(
            target_component="FilterPanel",
            required_props=("filters",),
            expected_shapes=(("filters", "array"),),
        )
        assert r.shape_for("filters") == "array"
        assert r.shape_for("other") is None

    def test_defaults_empty(self):
        r = BindingRequirement(target_component="X")
        assert r.required_props == ()
        assert r.expected_shapes == ()
        assert r.shape_for("anything") is None

    def test_to_dict_is_stable(self):
        r = BindingRequirement(
            target_component="FilterPanel",
            required_props=("filters",),
            expected_shapes=(("filters", "array<string>"),),
        )
        assert r.to_dict() == {
            "target_component": "FilterPanel",
            "required_props": ["filters"],
            "expected_shapes": {"filters": "array<string>"},
            "incompatibilities": [],
        }


# ═══════════════════════════════════════════════════════════════════
# A.6 — BindingProposal ≠ DataBinding
# ═══════════════════════════════════════════════════════════════════


class TestBindingProposal:
    def test_unresolved_never_produces_binding(self):
        p = BindingProposal(status="unresolved", target_component="FilterPanel")
        assert p.to_binding() is None

    def test_auto_unique_converts_to_binding(self):
        p = BindingProposal(
            status="auto_unique",
            target_component="FilterPanel",
            source=DataSourceRef(kind="slice", ref="slice:Page.filters", selector="filters"),
            schema=DataSchemaRef(ref="data_access:Page.slices", shape="array", fields=("filters",)),
            mapping=(DataMappingEntry(prop="filters", from_field="filters"),),
        )
        b = p.to_binding()
        assert isinstance(b, DataBinding)
        assert b.mapping_for("filters").from_field == "filters"

    def test_proposal_without_mapping_does_not_convert(self):
        p = BindingProposal(
            status="auto_unique",
            target_component="FilterPanel",
            source=DataSourceRef(kind="hook", ref="hook:useDashboardData"),
        )
        assert p.to_binding() is None

    def test_needs_choice_can_convert_after_human_selection(self):
        p = BindingProposal(
            status="needs_choice",
            target_component="FilterPanel",
            source=DataSourceRef(kind="hook", ref="hook:useSalesData"),
            mapping=(DataMappingEntry(prop="filters", from_field="filters"),),
        )
        assert isinstance(p.to_binding(), DataBinding)

    def test_to_dict_exposes_status_and_evidence(self):
        p = BindingProposal(
            status="needs_choice",
            target_component="FilterPanel",
            evidence=(EvidenceItem(kind="declared_dataslice", ref="slice:Page.filters"),),
        )
        d = p.to_dict()
        assert d["status"] == "needs_choice"
        assert d["evidence"][0]["kind"] == "declared_dataslice"

    def test_is_distinct_type_from_databinding(self):
        assert BindingProposal is not DataBinding
        assert "status" in {f for f in BindingProposal.__dataclass_fields__}
        assert "status" not in DataBinding.__dataclass_fields__


# ═══════════════════════════════════════════════════════════════════
# A.7 — Evidence (nunca autoridad)
# ═══════════════════════════════════════════════════════════════════


class TestEvidenceItem:
    def test_roundtrip(self):
        e = EvidenceItem(kind="declared_dataslice", ref="slice:Page.filters", detail="selector=filters")
        assert EvidenceItem(**e.to_dict()) == e

    def test_detail_omitted_when_none(self):
        assert EvidenceItem(kind="existing_import", ref="hook:x").to_dict() == {
            "kind": "existing_import",
            "ref": "hook:x",
        }


# ═══════════════════════════════════════════════════════════════════
# A.8 — Transporte zero-loss IntentAction → ConfirmedIntent
# ═══════════════════════════════════════════════════════════════════


class TestZeroLossTransport:
    def test_intent_action_defaults_to_none(self):
        assert IntentAction(verb="create", target_capability="c").binding is None

    def test_confirmed_intent_roundtrip_preserves_binding(self):
        b = _slice_binding()
        ci = ConfirmedIntent(
            contract_id="analytics.filter",
            contract_version=1,
            actions=[IntentAction(verb="create", target_capability="presentation.filter_panel", binding=b)],
            params={"filters": []},
            user_message="add filter panel",
            interpretation_id="i1",
        )
        rt = ConfirmedIntent.from_dict(ci.to_dict())
        assert rt.actions[0].binding == b

    def test_confirmed_intent_without_binding_stays_none(self):
        ci = ConfirmedIntent(
            contract_id="analytics.filter",
            contract_version=1,
            actions=[IntentAction(verb="create", target_capability="presentation.filter_panel")],
            params={},
            user_message="m",
            interpretation_id="i1",
        )
        rt = ConfirmedIntent.from_dict(ci.to_dict())
        assert rt.actions[0].binding is None

    def test_binding_survives_asdict_path(self):
        """asdict() es la serialización real de ConfirmedIntent.to_dict().

        Puede producir tuplas y campos opcionales explícitos; lo que debe
        garantizarse es que el binding se reconstruye idéntico.
        """
        b = _hook_binding()
        a = IntentAction(verb="modify", target_capability="presentation.kpi_row", binding=b)
        assert DataBinding.from_dict(asdict(a)["binding"]) == b


# ═══════════════════════════════════════════════════════════════════
# A.9 — PlanCompiler transport
# ═══════════════════════════════════════════════════════════════════


class TestPlanCompilerTransport:
    def _compile(self, action: IntentAction):
        ci = ConfirmedIntent(
            contract_id="analytics.filter",
            contract_version=1,
            actions=[action],
            params={"filters": []},
            user_message="m",
            interpretation_id="i1",
        )
        return compile_plan(ci)

    def test_binding_travels_to_plan_actions(self):
        b = _slice_binding()
        plan = self._compile(
            IntentAction(verb="create", target_capability="presentation.filter_panel", binding=b)
        )
        assert plan.actions[0]["binding"] == b.to_dict()

    def test_binding_travels_to_semantic_frame(self):
        b = _slice_binding()
        plan = self._compile(
            IntentAction(verb="create", target_capability="presentation.filter_panel", binding=b)
        )
        frame = plan.semantic_frame["actions"][0]
        assert frame["binding"] == b.to_dict()

    def test_absent_binding_is_not_emitted(self):
        plan = self._compile(
            IntentAction(verb="create", target_capability="presentation.filter_panel")
        )
        assert "binding" not in plan.actions[0]
        assert "binding" not in plan.semantic_frame["actions"][0]

    def test_binding_does_not_alter_what_authority(self):
        b = _slice_binding()
        plan = self._compile(
            IntentAction(verb="create", target_capability="presentation.filter_panel", binding=b)
        )
        assert plan.actions[0]["target_capability"] == "presentation.filter_panel"
        assert plan.semantic_frame["actions"][0]["target_capability"] == "presentation.filter_panel"

    def test_binding_does_not_alter_params(self):
        b = _slice_binding()
        plan = self._compile(
            IntentAction(
                verb="create",
                target_capability="presentation.filter_panel",
                params={"filters": ["a"]},
                binding=b,
            )
        )
        assert plan.actions[0]["params"] == {"filters": ["a"]}

    def test_plan_is_deterministic(self):
        b = _hook_binding()
        a = IntentAction(verb="modify", target_capability="presentation.kpi_row", binding=b)
        assert self._compile(a).to_dict() == self._compile(a).to_dict()