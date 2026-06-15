"""Unit tests for StructuralPatch, apply_patch, and _build_modify_patch."""
import pytest
from app.graphir.constraint.renderer import (
    StructuralPatch,
    _build_modify_patch,
    apply_patch,
    _parse_interface_block,
    _insert_after_interface,
    _insert_import,
)
from app.graphir.backends.react_backend import COMPONENT_DESTRUCTURE


# ── Fixtures ──────────────────────────────────────────────────────────

KPIROW_EXISTING = """\
import { KpiCard } from './KpiCard';
import styles from './KpiRow.module.css';
import React from 'react';

interface KpiRowProps {
  data: KpiItem[];
}

export const KpiRow: React.FC<KpiRowProps> = ({ data }) => {
  return (
    <div className={styles.row}>
      {data.map((item) => (
        <KpiCard key={item} value={item.value} label={item.label} />
      ))}
    </div>
  );
};
"""

KPIROW_EXISTING_OLD_NAMES = """\
import { KpiCard } from './KpiCard';
import styles from './KpiRow.module.css';
import React from 'react';

interface KpiRowProps {
  metrics: string[];
}

export const KpiRow: React.FC<KpiRowProps> = ({ metrics }) => {
  return (
    <div className={styles.row}>
      {metrics.map((item) => (
        <KpiCard key={item} value={item.value} label={item.label} />
      ))}
    </div>
  );
};
"""

NEW_INTERFACE = """\
interface KpiRowProps {
  data: KpiItem[];
}"""

SIG_KPIROW = {
    "props": NEW_INTERFACE,
    "prop_names": ["data"],
    "extra_types": ["import type { KpiItem } from '@/types';"],
}


# ── apply_patch — structural preservation ─────────────────────────────


class TestApplyPatch:
    def test_preserves_imports(self):
        patch = StructuralPatch(component_type="KpiRow")
        result = apply_patch(KPIROW_EXISTING, patch)
        assert "import { KpiCard } from './KpiCard'" in result
        assert "import styles from './KpiRow.module.css'" in result
        assert "import React from 'react'" in result

    def test_replaces_interface(self):
        patch = StructuralPatch(
            component_type="KpiRow",
            props_interface=NEW_INTERFACE,
        )
        result = apply_patch(KPIROW_EXISTING_OLD_NAMES, patch)
        assert "interface KpiRowProps" in result
        assert "data: KpiItem[]" in result
        assert "metrics:" not in result

    def test_renames_body_vars(self):
        patch = StructuralPatch(
            component_type="KpiRow",
            rename_map={"metrics": "data"},
        )
        result = apply_patch(KPIROW_EXISTING_OLD_NAMES, patch)
        assert "({ data })" in result
        assert "data.map((item)" in result

    def test_preserves_body_structure(self):
        patch = StructuralPatch(component_type="KpiRow")
        result = apply_patch(KPIROW_EXISTING, patch)
        assert "KpiCard" in result
        assert "styles.row" in result
        assert "item.value" in result

    def test_empty_rename_map(self):
        patch = StructuralPatch(component_type="KpiRow")
        result = apply_patch(KPIROW_EXISTING, patch)
        # Body should be identical to original
        assert "data.map((item)" in result

    def test_no_interface_no_change(self):
        patch = StructuralPatch(component_type="KpiRow")
        result = apply_patch(KPIROW_EXISTING_OLD_NAMES, patch)
        assert "metrics:" in result  # interface not touched

    def test_adds_extra_types(self):
        patch = StructuralPatch(
            component_type="KpiRow",
            extra_types=["type KpiItem = { value: number; label: string; }"],
        )
        result = apply_patch(KPIROW_EXISTING, patch)
        assert "type KpiItem" in result

    def test_adds_data_imports(self):
        patch = StructuralPatch(
            component_type="KpiRow",
            data_imports=["import { fetchKpiData } from '@/lib/api';"],
        )
        result = apply_patch(KPIROW_EXISTING, patch)
        assert "fetchKpiData" in result

    def test_word_boundary_no_false_positive(self):
        content = """\
const metricsData = getMetrics();
const metrics = [];
"""
        patch = StructuralPatch(
            component_type="KpiRow",
            rename_map={"metrics": "data"},
        )
        result = apply_patch(content, patch)
        assert "metricsData" in result  # not renamed
        assert "getMetrics()" in result  # not renamed
        assert "const data =" in result  # standalone word renamed

    def test_parse_interface_roundtrip(self):
        start, end, block = _parse_interface_block(KPIROW_EXISTING, "KpiRow")
        lines = KPIROW_EXISTING.split("\n")
        lines[start:end + 1] = NEW_INTERFACE.split("\n")
        result = "\n".join(lines)
        assert "data: KpiItem[]" in result
        assert "interface KpiRowProps" in result


# ── _build_modify_patch ───────────────────────────────────────────────


class TestBuildModifyPatch:
    def test_from_signature(self):
        patch = _build_modify_patch("KpiRow", SIG_KPIROW, [])
        assert patch.props_interface == NEW_INTERFACE
        assert "import type { KpiItem }" in patch.extra_types[0]

    def test_no_signature_returns_empty_patch(self):
        patch = _build_modify_patch("KpiRow", None, [])
        assert patch.props_interface is None
        assert patch.rename_map == {}

    def test_rename_map_from_signature(self):
        sig = {"props": "interface X { data: KpiItem[] }", "prop_names": ["data"]}
        patch = _build_modify_patch("KpiRow", sig, [])
        assert patch.rename_map == {"metrics": "data"}

    def test_empty_prop_names_empty_map(self):
        sig = {"props": "interface X { data: KpiItem[] }", "prop_names": []}
        patch = _build_modify_patch("KpiRow", sig, [])
        assert patch.rename_map == {}


# ── COMPONENT_DESTRUCTURE registry coverage ──────────────────────────


class TestComponentDestructureRegistry:
    """Every generator component type must have a destructure entry.

    If a generator is added to ComponentGenerator dispatch without an entry
    here, MODIFY will not compute the correct rename_map for it.
    """

    def test_all_registered_types_have_entry(self):
        from app.graphir.backends.react_backend import ReactBackend
        registered = ReactBackend.registered_types()
        # Page is a compositor — no props destructure, excluded from registry
        for ctype in sorted(registered):
            if ctype == "Page":
                continue
            assert ctype in COMPONENT_DESTRUCTURE, (
                f"Component {ctype!r} has no COMPONENT_DESTRUCTURE entry. "
                "Add it to keep MODIFY rename_map in sync."
            )

    def test_page_excluded_from_registry(self):
        assert "Page" not in COMPONENT_DESTRUCTURE

    def test_all_entries_exist(self):
        assert "KpiRow" in COMPONENT_DESTRUCTURE
        assert "Timeseries" in COMPONENT_DESTRUCTURE
        assert len(COMPONENT_DESTRUCTURE) >= 11
