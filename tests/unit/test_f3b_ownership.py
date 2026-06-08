"""F3b ownership — type-gated binding-wins per-prop.

Tests verify:
1. Binding-wins for eligible props (string, string[]): binding overrides slice
2. Non-eligible skip (array of objects, object): slice stays authoritative
3. BINDING_WINS log emission when binding overrides slice

Ownership boundary is deterministic: is_binding_eligible() on registry type_info
is the SSOT. No runtime ambiguity.
"""

from __future__ import annotations

import logging

import pytest

from app.binding.resolver import resolve as resolve_bindings, is_binding_eligible
from app.graphir.backends.react_backend import JSVariable
from app.signature.prop_mapper import DataSourceIR, DataSlice


def _make_ds(slices: list[tuple[str, str, str, tuple[str, ...]]]) -> DataSourceIR:
    """Build DataSourceIR from (component, target_prop, selector, consumes) tuples."""
    return DataSourceIR(
        type="dashboard_data",
        selector=None,
        slices=tuple(
            DataSlice(component=comp, target_prop=prop, selector=sel, consumes=cons)
            for comp, prop, sel, cons in slices
        ),
    )


class TestF3bBindingWins:
    """Eligible prop (string): binding overrides slice."""

    def test_binding_wins_for_string_prop(self):
        """SearchBar.placeholder (string type) — binding overrides slice."""
        ds = _make_ds([
            ("SearchBar", "placeholder", "searchText", ()),
        ])
        result = resolve_bindings(
            {"placeholder": "Type here..."},
            page_ds_override=ds,
        )
        # Binding should win: "Type here..." from contract, not JSVariable from slice
        assert "SearchBar" in result.component_props
        placeholder = result.component_props["SearchBar"]["placeholder"]
        assert placeholder == "Type here...", (
            f"Expected binding value 'Type here...', got {placeholder!r}"
        )
        assert not isinstance(placeholder, JSVariable), (
            "Binding-eligible prop should not retain JSVariable slice reference"
        )

    def test_is_binding_eligible_string(self):
        """Verify is_binding_eligible for string type."""
        assert is_binding_eligible({"type": "string"}) is True

    def test_binding_wins_for_untyped_prop(self):
        """Untyped binding (type_info=None) — binding overrides slice (assumed scalar)."""
        ds = _make_ds([
            ("ExportButton", "format", "exportFormat", ()),
        ])
        result = resolve_bindings(
            {"format": "pdf"},
            page_ds_override=ds,
        )
        assert "ExportButton" in result.component_props
        assert result.component_props["ExportButton"]["format"] == "pdf"


class TestF3bNonEligibleSkip:
    """Non-eligible prop (array of objects, object): slice stays authoritative."""

    def test_slice_wins_for_array_of_objects(self):
        """KpiRow.data (KpiItem[]) — slice stays, binding is skipped."""
        ds = _make_ds([
            ("KpiRow", "data", "kpiData", ("metrics",)),
        ])
        result = resolve_bindings(
            {"metrics": ["revenue", "growth"]},
            page_ds_override=ds,
        )
        assert "KpiRow" in result.component_props
        data_val = result.component_props["KpiRow"]["data"]
        assert isinstance(data_val, JSVariable), (
            f"Expected JSVariable slice ref, got {type(data_val).__name__}: {data_val!r}"
        )
        assert data_val.name == "_pageData.kpiData"

    def test_is_binding_eligible_array_of_objects(self):
        """Verify is_binding_eligible for array of non-primitive objects."""
        assert is_binding_eligible({"type": "array", "items": "KpiItem"}) is False
        assert is_binding_eligible({"type": "array", "items": "Point"}) is False

    def test_is_binding_eligible_object(self):
        """Verify is_binding_eligible for object type (CONDITIONAL, not YES)."""
        assert is_binding_eligible({"type": "object"}) is False


class TestF3bLogEmission:
    """BINDING_WINS log emitted when binding overrides slice."""

    def test_binding_wins_log_emitted(self, caplog):
        """BINDING_WINS debug log appears when binding overrides slice."""
        caplog.set_level(logging.DEBUG)
        ds = _make_ds([
            ("SearchBar", "placeholder", "searchText", ()),
        ])
        resolve_bindings(
            {"placeholder": "Type here..."},
            page_ds_override=ds,
        )
        binding_wins_logs = [
            r for r in caplog.records
            if r.name == "app.binding.resolver" and "BINDING_WINS" in r.getMessage()
        ]
        assert len(binding_wins_logs) >= 1, (
            "Expected at least one BINDING_WINS log when binding overrides slice"
        )
        log_msg = binding_wins_logs[0].getMessage()
        assert "SearchBar.placeholder" in log_msg
        assert "type_info" in log_msg
        assert "overrides slice" in log_msg

    def test_no_binding_wins_log_when_not_eligible(self, caplog):
        """No BINDING_WINS log when prop is not binding-eligible."""
        caplog.set_level(logging.DEBUG)
        ds = _make_ds([
            ("KpiRow", "data", "kpiData", ("metrics",)),
        ])
        resolve_bindings(
            {"metrics": ["revenue"]},
            page_ds_override=ds,
        )
        binding_wins_logs = [
            r for r in caplog.records
            if r.name == "app.binding.resolver" and "BINDING_WINS" in r.getMessage()
        ]
        assert len(binding_wins_logs) == 0, (
            "No BINDING_WINS log expected for non-eligible prop"
        )
