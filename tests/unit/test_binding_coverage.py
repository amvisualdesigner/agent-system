"""STEP 0: Coverage gate — verifica bindings antes del kill switch.

Cada componente registrado en el sistema (generators + intent mapping)
DEBE tener un binding en data_access.json v4 antes de activar STEP 1.

Si este test falla, hay que expandir data_access.json antes de eliminar
el fallback props = dict(node.data) en compiler.py.
"""

import json
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Todos los graphir types que aparecen en ejecución real
# Fuente: backend/app/graphir/intent.py (_CAPABILITY_TO_GRAPHIR_TYPE)
#         + backend/app/graphir/backends/react_backend.py (register calls)
REQUIRED_COMPONENTS: set[str] = {
    "Page",          # layout.page — data owner
    "KpiRow",        # presentation.kpi_row
    "Timeseries",    # presentation.timeseries
    "AnalyticsTable", # presentation.table
    "BarChart",      # presentation.chart.bar
    "MetricCard",    # presentation.metric_card
    "FilterPanel",   # presentation.filter_panel
    "Embed",         # presentation.embed
    "SearchBar",     # interaction.search
    "Form",          # interaction.form
    "ExportButton",  # data.export
    "Drilldown",     # data.drilldown
}

DATA_ACCESS_PATH = Path(__file__).resolve().parents[2] / "backend" / "config" / "data_access.json"


def load_data_access() -> dict | None:
    """Load data_access.json, return dict or None."""
    if not DATA_ACCESS_PATH.exists():
        logger.warning("data_access.json not found at %s", DATA_ACCESS_PATH)
        return None
    try:
        with open(DATA_ACCESS_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict) or "components" not in data:
            logger.warning("data_access.json: missing 'components' key")
            return None
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("data_access.json: failed to load — %s", e)
        return None


def components_with_bindings(data: dict) -> set[str]:
    """Extract set of component types that have bindings in data_access.json.

    Handles:
      - v3: components.Page.dataSource.slices → slice targets bound
      - v4: components.X.props → per-component bindings
      - v4: composition.X.dataSource → composition data owner
    """
    components = data.get("components", {})
    bound: set[str] = set()

    for comp_name, comp_data in components.items():
        if not isinstance(comp_data, dict):
            continue

        # v4: component has direct 'props' entry → has binding
        if "props" in comp_data and comp_data["props"]:
            bound.add(comp_name)
            continue

        # v3: Page has dataSource.slices → slice targets have bindings
        ds = comp_data.get("dataSource")
        if ds and isinstance(ds, dict):
            slices = ds.get("slices", [])
            for slice_ in slices:
                target = slice_.get("component")
                if target:
                    bound.add(target)
            # Page itself is the data owner → has binding
            bound.add(comp_name)

    # v4: composition section — data owner has binding, slices provide data to targets
    comp = data.get("composition", {})
    if isinstance(comp, dict):
        for comp_name, comp_data in comp.items():
            bound.add(comp_name)
            if isinstance(comp_data, dict):
                ds = comp_data.get("dataSource")
                if ds and isinstance(ds, dict):
                    slices = ds.get("slices", [])
                    for slice_ in slices:
                        target = slice_.get("component")
                        if target:
                            bound.add(target)

    return bound


def get_missing_bindings(data: dict) -> set[str]:
    """Return set of REQUIRED_COMPONENTS that have no binding."""
    bound = components_with_bindings(data)
    return REQUIRED_COMPONENTS - bound


# ── Tests ──────────────────────────────────────────────────────────────────


def test_binding_coverage_gate():
    """Every production component MUST have a binding before kill switch."""
    data = load_data_access()
    assert data is not None, (
        f"data_access.json not found at {DATA_ACCESS_PATH}. "
        f"Cannot verify coverage gate."
    )
    missing = get_missing_bindings(data)
    assert not missing, (
        f"Cannot kill fallback: {len(missing)} components have no binding. "
        f"Missing: {sorted(missing)}. "
        f"Expand data_access.json v4 bindings before enabling STEP 1."
    )


def test_binding_coverage_list():
    """Report all component binding status (info, not blocking)."""
    data = load_data_access()
    if data is None:
        return  # skip silently

    bound = components_with_bindings(data)
    missing = REQUIRED_COMPONENTS - bound
    present = REQUIRED_COMPONENTS & bound

    print(f"\nBinding coverage: {len(present)}/{len(REQUIRED_COMPONENTS)}")
    print(f"  With binding: {sorted(present)}")
    if missing:
        print(f"  WITHOUT binding: {sorted(missing)}  ⚠️")
    print()
