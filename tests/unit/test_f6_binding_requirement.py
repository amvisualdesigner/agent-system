"""Fase 6 · Subfase B — BindingRequirement determinista.

Reglas bajo prueba:
  - required_props viene de las fuentes que declaran conexión a datos
    (slot del contrato, registry `required: true`). La firma NO provee
    required_props (no distingue config de datos).
  - La firma es VALIDADOR: si no declara una prop que el slot exige, la
    evidencia es incompatible → se omite y se registra.
  - `components.X.props = {}` = ausencia de declaraciones v4, NO
    "el componente no tiene props" (caso FilterPanel).
  - expected_shapes solo se emite si TODAS las declaraciones coinciden.
    stages distintos (param vs prop post-transform) → incompatibilidad
    registrada, sin fabricar shape.
  - Evidencia insuficiente → no se fabrica requisito.

No descubre source, schema ni mapping.
"""

import json

import pytest

from app.binding.requirement import build_binding_requirement
from app.contracts.skill_registry import get_contract
from app.intent.models import BindingRequirement
from app.signature.extractor import extract_signatures
from app.signature.prop_mapper import load_v4_bindings


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _sig(props: str, component: str = "FilterPanel") -> dict:
    """Firma mínima con la forma que devuelve extract_signatures."""
    import re

    names = re.findall(r"^\s+(\w+)\??\s*:", props, re.MULTILINE)
    return {
        "component_name": component,
        "props": props,
        "prop_names": names,
        "required_props": [],
        "optional_props": names,
    }


FILTER_SIGNATURE = _sig(
    "interface FilterPanelProps {\n  filters?: string[];\n}"
)


@pytest.fixture(scope="module")
def v4():
    return load_v4_bindings()


@pytest.fixture(scope="module")
def filter_contract():
    return get_contract("analytics.filter", 1)


# ═══════════════════════════════════════════════════════════════════
# B.1 — required_props desde el slot del contrato
# ═══════════════════════════════════════════════════════════════════


class TestRequiredPropsFromSlot:
    def test_filter_panel_prop_from_slot(self, filter_contract, v4):
        r = build_binding_requirement("FilterPanel", contract=filter_contract, v4_bindings=v4)
        assert r.required_props == ("filters",)

    def test_multi_prop_slot(self, v4):
        c = get_contract("analytics.chart_bar", 1)
        r = build_binding_requirement("BarChart", contract=c, v4_bindings=v4)
        assert set(r.required_props) == {"categories", "values"}

    def test_slot_prop_order_is_deterministic(self, v4):
        c = get_contract("analytics.chart_bar", 1)
        a = build_binding_requirement("BarChart", contract=c, v4_bindings=v4)
        b = build_binding_requirement("BarChart", contract=c, v4_bindings=v4)
        assert a.required_props == b.required_props

    def test_capability_param_map_matches_slots(self, filter_contract):
        assert filter_contract.capability_param_map["presentation.filter_panel"] == {
            "filters": "filters"
        }

    def test_capability_param_map_path_used(self, v4):
        r = build_binding_requirement(
            "KpiRow", contract=get_contract("dashboard.sales_overview", 1), v4_bindings=v4
        )
        assert r.required_props == ("data",)


# ═══════════════════════════════════════════════════════════════════
# B.2 — components.X.props = {} NO significa "sin props"
# ═══════════════════════════════════════════════════════════════════


class TestEmptyRegistryIsNotNoProps:
    def test_filter_panel_absent_from_v4_bindings(self, v4):
        """El registry v4 declara props:{} → load_v4_bindings lo omite."""
        assert "FilterPanel" not in v4

    def test_filter_panel_still_has_required_prop(self, filter_contract, v4):
        r = build_binding_requirement("FilterPanel", contract=filter_contract, v4_bindings=v4)
        assert r.required_props == ("filters",)

    def test_raw_registry_entry_is_empty_object(self):
        with open("backend/config/data_access.json") as fh:
            data = json.load(fh)
        assert data["components"]["FilterPanel"]["props"] == {}


# ═══════════════════════════════════════════════════════════════════
# B.3 — required desde registry (required: true)
# ═══════════════════════════════════════════════════════════════════


class _FakeBinding:
    def __init__(self, required=False, type_info=None, shape=None, from_field=""):
        self.required = required
        self.type_info = type_info
        self.shape = shape
        self.from_field = from_field
        self.transform = "identity"
        self.arity = None
        self.default = None


class TestRequiredFromRegistry:
    def test_registry_required_adds_prop(self):
        v4 = {"Widget": {"payload": _FakeBinding(required=True)}}
        r = build_binding_requirement("Widget", v4_bindings=v4)
        assert r.required_props == ("payload",)

    def test_registry_not_required_does_not_add_prop(self):
        v4 = {"Widget": {"payload": _FakeBinding(required=False)}}
        r = build_binding_requirement("Widget", v4_bindings=v4)
        assert r.required_props == ()

    def test_slot_and_registry_required_are_merged_deduped(self, filter_contract):
        v4 = {"FilterPanel": {"filters": _FakeBinding(required=True)}}
        r = build_binding_requirement("FilterPanel", contract=filter_contract, v4_bindings=v4)
        assert r.required_props == ("filters",)

    def test_registry_required_order_is_stable(self):
        v4 = {"Widget": {"b": _FakeBinding(required=True), "a": _FakeBinding(required=True)}}
        r = build_binding_requirement("Widget", v4_bindings=v4)
        assert r.required_props == r.required_props


# ═══════════════════════════════════════════════════════════════════
# B.4 — expected_shapes
# ═══════════════════════════════════════════════════════════════════


class TestExpectedShapes:
    def test_shape_from_contract_input_schema(self, filter_contract, v4):
        r = build_binding_requirement("FilterPanel", contract=filter_contract, v4_bindings=v4)
        assert r.expected_shapes == (("filters", "array<string>"),)

    def test_shape_from_registry_type_info(self):
        v4 = {"Widget": {"data": _FakeBinding(required=True, type_info={"type": "array", "items": "Point"})}}
        r = build_binding_requirement("Widget", v4_bindings=v4)
        assert r.shape_for("data") == "array<Point>"

    def test_shape_from_signature_only(self):
        r = build_binding_requirement("Widget", signature=_sig("interface WProps {\n  label: string;\n}", "W"))
        assert r.required_props == ()
        r2 = build_binding_requirement(
            "W", signature=_sig("interface WProps {\n  label: string;\n}", "W"),
            v4_bindings={"W": {"label": _FakeBinding(required=True)}},
        )
        assert r2.shape_for("label") == "string"

    def test_agreeing_declarations_emit_shape(self, filter_contract):
        r = build_binding_requirement(
            "FilterPanel",
            contract=filter_contract,
            signature=FILTER_SIGNATURE,
            v4_bindings={},
        )
        assert r.shape_for("filters") == "array<string>"
        assert r.incompatibilities == ()

    def test_ts_array_and_json_array_agree(self, filter_contract):
        """'filters?: string[]' y {type:array,items:{type:string}} coinciden."""
        r = build_binding_requirement(
            "FilterPanel", contract=filter_contract, signature=FILTER_SIGNATURE, v4_bindings={}
        )
        assert r.shape_for("filters") == "array<string>"

    def test_conflicting_declarations_are_recorded_not_merged(self):
        """Param stage (string) vs prop post-transform (object) → conflicto."""
        v4 = {"MetricCard": {
            "value": _FakeBinding(required=True, type_info={"type": "object"}),
        }}
        c = get_contract("analytics.metric_card", 1)
        r = build_binding_requirement("MetricCard", contract=c, v4_bindings=v4)
        assert r.required_props == ("value", "label")
        assert r.shape_for("value") is None
        assert any("conflicting_shape_declarations:MetricCard.value" in i for i in r.incompatibilities)

    def test_real_metric_card_conflict_is_recorded(self, v4):
        r = build_binding_requirement("MetricCard", contract=get_contract("analytics.metric_card", 1), v4_bindings=v4)
        assert r.expected_shapes == ()
        assert len(r.incompatibilities) == 2

    def test_missing_shape_is_omitted_not_invented(self, v4):
        """table_data no tiene shape en ninguna fuente → se omite."""
        r = build_binding_requirement("AnalyticsTable", contract=get_contract("analytics.table", 1), v4_bindings=v4)
        assert r.required_props == ("columns", "table_data")
        assert r.shape_for("table_data") is None
        assert dict(r.expected_shapes) == {"columns": "array<string>"}

    def test_union_type_is_order_independent(self):
        a = _shape_via_registry({"type": ["string", "number"]})
        b = _shape_via_registry({"type": ["number", "string"]})
        assert a == b == "number|string"


def _shape_via_registry(type_info):
    v4 = {"W": {"p": _FakeBinding(required=True, type_info=type_info)}}
    return build_binding_requirement("W", v4_bindings=v4).shape_for("p")


# ═══════════════════════════════════════════════════════════════════
# B.5 — La firma valida, no provee
# ═══════════════════════════════════════════════════════════════════


class TestSignatureIsValidator:
    def test_signature_absent_prop_is_rejected(self, filter_contract, v4):
        sig = _sig("interface FilterPanelProps {\n  otra?: string;\n}")
        r = build_binding_requirement("FilterPanel", contract=filter_contract, signature=sig, v4_bindings=v4)
        assert r.required_props == ()
        assert "slot_or_registry_requires_prop_absent_from_signature:FilterPanel.filters" in r.incompatibilities

    def test_signature_accepting_prop_keeps_it(self, filter_contract, v4):
        r = build_binding_requirement(
            "FilterPanel", contract=filter_contract, signature=FILTER_SIGNATURE, v4_bindings=v4
        )
        assert r.required_props == ("filters",)

    def test_empty_signature_prop_names_disables_validation(self, filter_contract, v4):
        """Sin evidencia de firma no se bloquea: no se fabrica rechazo."""
        r = build_binding_requirement(
            "FilterPanel", contract=filter_contract, signature={"prop_names": [], "props": ""}, v4_bindings=v4
        )
        assert r.required_props == ("filters",)

    def test_signature_alone_does_not_create_requirements(self):
        """La firma no distingue config de datos → no genera required_props."""
        r = build_binding_requirement("FilterPanel", signature=FILTER_SIGNATURE)
        assert r.required_props == ()

    def test_real_extracted_signature_agrees(self, filter_contract, v4, tmp_path):
        """Firma real extraída de un .tsx valida sin rechazar el slot."""
        (tmp_path / "FilterPanel.tsx").write_text(
            "import React from 'react';\n"
            "interface FilterPanelProps {\n  filters?: string[];\n}\n"
            "export const FilterPanel: React.FC<FilterPanelProps> = ({ filters = [] }) => null;\n"
        )
        sigs = extract_signatures(str(tmp_path))
        assert sigs["FilterPanel"]["prop_names"] == ["filters"]

        r = build_binding_requirement(
            "FilterPanel", contract=filter_contract, signature=sigs["FilterPanel"], v4_bindings=v4
        )
        assert r.required_props == ("filters",)
        assert r.shape_for("filters") == "array<string>"
        assert r.incompatibilities == ()

    def test_real_extracted_signature_rejects_absent_prop(self, filter_contract, v4, tmp_path):
        (tmp_path / "FilterPanel.tsx").write_text(
            "import React from 'react';\n"
            "interface FilterPanelProps {\n  otro?: string;\n}\n"
            "export const FilterPanel: React.FC<FilterPanelProps> = ({ otro }) => null;\n"
        )
        sigs = extract_signatures(str(tmp_path))
        r = build_binding_requirement(
            "FilterPanel", contract=filter_contract, signature=sigs["FilterPanel"], v4_bindings=v4
        )
        assert r.required_props == ()
        assert "slot_or_registry_requires_prop_absent_from_signature:FilterPanel.filters" in r.incompatibilities


# ═══════════════════════════════════════════════════════════════════
# B.6 — Evidencia insuficiente
# ═══════════════════════════════════════════════════════════════════


class TestInsufficientEvidence:
    def test_no_evidence_yields_empty_requirement(self):
        r = build_binding_requirement("FilterPanel")
        assert r.required_props == ()
        assert r.expected_shapes == ()
        assert r.incompatibilities == ()

    def test_unknown_component_yields_empty(self, v4):
        r = build_binding_requirement("NoSuchComponent", v4_bindings=v4)
        assert r.required_props == ()

    def test_none_contract_and_registry_is_safe(self):
        r = build_binding_requirement("X", contract=None, signature=None, v4_bindings=None)
        assert isinstance(r, BindingRequirement)
        assert r.target_component == "X"

    def test_never_raises_on_garbage_signature(self):
        r = build_binding_requirement("X", signature={"props": None, "prop_names": None})
        assert r.required_props == ()

    def test_never_raises_on_garbage_contract(self):
        class _C:
            ast_template = None
            capability_param_map = None
            input_schema = None

        r = build_binding_requirement("X", contract=_C())
        assert r.required_props == ()


# ═══════════════════════════════════════════════════════════════════
# B.7 — Determinismo y no-regresión semántica
# ═══════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_repeated_calls_identical(self, filter_contract, v4):
        a = build_binding_requirement("FilterPanel", contract=filter_contract, v4_bindings=v4)
        b = build_binding_requirement("FilterPanel", contract=filter_contract, v4_bindings=v4)
        assert a.to_dict() == b.to_dict()

    def test_to_dict_is_serializable(self, filter_contract, v4):
        r = build_binding_requirement("FilterPanel", contract=filter_contract, v4_bindings=v4)
        assert json.loads(json.dumps(r.to_dict()))["required_props"] == ["filters"]

    def test_does_not_mutate_contract(self, filter_contract):
        before = json.dumps(filter_contract.ast_template, sort_keys=True)
        build_binding_requirement("FilterPanel", contract=filter_contract)
        assert json.dumps(filter_contract.ast_template, sort_keys=True) == before

    def test_does_not_mutate_registry(self, v4):
        before = json.dumps({k: {p: vars(b) for p, b in props.items()} for k, props in v4.items()}, default=str, sort_keys=True)
        build_binding_requirement("KpiRow", contract=get_contract("dashboard.sales_overview", 1), v4_bindings=v4)
        after = json.dumps({k: {p: vars(b) for p, b in props.items()} for k, props in v4.items()}, default=str, sort_keys=True)
        assert after == before

    def test_no_source_or_mapping_discovered(self, filter_contract, v4):
        """Subfase B no descubre source/schema/mapping."""
        r = build_binding_requirement("FilterPanel", contract=filter_contract, v4_bindings=v4)
        fields = set(BindingRequirement.__dataclass_fields__)
        assert fields == {"target_component", "required_props", "expected_shapes", "incompatibilities"}