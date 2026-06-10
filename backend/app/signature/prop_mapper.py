"""PropMapper — translates contract params to component props.

Phase 5.5: Semantic Prop Binding via typed BindingIR.
Maps semantic contract params → real component props using:
  1. BindingIR (data_access.json bindings — typed, no string expressions)
  2. PARAM_ALIASES (safe primitive alias fallback)
  3. Title heuristic (timeseries_metric → title)

Resolution state machine (per prop):
  RESOLVED          — explicit BindingIR match
  INFERRED           — safe alias or heuristic
  FALLBACK_ALLOWED   — optional prop, no binding needed
  BINDING_MISSING    — required prop, no binding → MUST NOT render silently

Regla cardinal:
  PropMapper traduce params semánticos → props de componente.
  NUNCA decide qué componente crear ni qué contrato elegir.
  NO toca StructuralIndex, PlanCompiler, ni IntentInterpreter.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class MISSING_REQUIRED_PROPS(Exception):
    """Raised when a component has required props but no binding resolves them.

    This is a hard BLOCKING error — the component MUST NOT be rendered
    without required props. The caller decides action (reject plan, etc.).

    Attributes:
        component: Component type name (e.g. "KpiRow").
        missing: List of required prop names not resolved.
        available: List of props that WERE resolved (for debugging).
        contract_params: Contract params available (for debugging).
    """
    def __init__(
        self,
        component: str,
        missing: list[str],
        available: list[str] | None = None,
        contract_params: list[str] | None = None,
    ):
        self.component = component
        self.missing = missing
        self.available = available or []
        self.contract_params = contract_params or []
        super().__init__(
            f"MISSING_REQUIRED_PROPS:{component} missing={missing} "
            f"available={self.available} contract_params={self.contract_params}"
        )

# ── BindingIR types ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class DataSlice:
    """A named data slice from a Page-level aggregator, destined for one child component.

    In Phase 6 (Composition Data Flow), the Page is the sole data owner.
    Each child receives exactly one slice from the Page's data graph.

    Fields:
        component: Target child component type name (e.g. "KpiRow").
        target_prop: Prop name on the child component (e.g. "data").
        selector: Dot-separated path to extract from Page hook result
                  (e.g. "kpiData", "chartData.timeseries").
        consumes: Contract param keys this slice depends on (for drift detection).
    """
    component: str
    target_prop: str
    selector: str
    consumes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataSourceIR:
    """Framework-agnostic data source identifier.
    
    Separates the WHAT (semantic data source) from the HOW (framework lowering).
    
    Types:
      "dashboard_data"  — Page-level aggregator data (useDashboardData in React,
                          dashboardData in Vue).
      "entity_detail"   — Single entity fetch (useEntity / entityDetail).
      "raw_selector"    — Direct path expression (store.some.value).
    
    Phase 6: When this represents the Page-level data source, `slices` contains
    DataSlice entries describing how to distribute data to children.
    The Page emits exactly ONE hook call + slice references per child.
    
    The framework-specific lowering (HookBinding, JSExpression) happens in
    compile_binding(), never in the IR itself.
    """
    type: str
    selector: str | None = None
    slices: tuple[DataSlice, ...] = ()


# ── Framework lowering maps (DataSourceIR type → framework expression) ──

_REACT_HOOK_MAP: dict[str, str] = {
    "dashboard_data": "useDashboardData",
}

_HOOK_IMPORT_MAP: dict[str, str] = {
    "useDashboardData": "import { useDashboardData } from '@/hooks/useDashboardData'",
}


@dataclass(frozen=True)
class Binding:
    """Single prop binding IR — deterministic → TS-safe expression.
    
    Uses DataSourceIR (framework-agnostic). Lowered to framework-specific
    expressions (HookBinding/JSExpression) by compile_binding().
    """
    target_prop: str
    source: DataSourceIR
    transform: str | None = None
    consumes: tuple[str, ...] = ()


@dataclass
class ComponentBinding:
    """All bindings for one component."""
    component: str
    bindings: list[Binding]


# ── Resolution state machine ──────────────────────────────────────────────


# ── JSExpression (kept for _emit compatibility) ──────────────────────────


@dataclass(frozen=True)
class JSExpression:
    """A pre-computed JavaScript expression from BindingIR compilation.

    Wrapped separately from plain strings so _emit knows to use `{}`
    JSX wrapping (JS expression) instead of `""` (string literal).
    """
    code: str


# ── HookBinding (structured, not string expression) ─────────────────────


@dataclass(frozen=True)
class HookBinding:
    """Structured hook binding result — NOT a raw expression.

    Represents a prop that should be resolved by calling a React hook
    and extracting a specific transform path. During rendering, this is
    hoisted to the parent component as a variable declaration.

    Fields:
        hook_name: Name of the hook function (e.g. "useDashboardData")
        transform: Dot-separated path to extract from hook result
                   (e.g. "kpiData"). None means use whole result.
        import_stmt: Full import statement for the hook, or None.
    """
    hook_name: str
    transform: str | None
    import_stmt: str | None


# ── v4 Binding format ───────────────────────────────────────────────────


@dataclass(frozen=True)
class V4BindingDef:
    """A single v4 binding definition for a component prop.

    Maps a contract param (from_field) to a component prop, with
    optional transform, default, and arity hint.

    Attributes:
        from_field: contract_params field this binding consumes
        transform: registered transform name (identity, items, wrap, value, label)
        arity: cardinality hint (scalar, array, scalar->array)
        shape: type hint (documentation only, legacy)
        type_info: structured type schema for binding eligibility.
            e.g. {"type": "string"} or {"type": "array", "items": "Point"}.
            None = untyped = binding eligible (assumed scalar/UI).
        default: fallback value if from_field not in contract_params
        required: if True and no from_field + no default → MISSING_CONTRACT_PARAM
    """
    from_field: str
    transform: str = "identity"
    arity: str | None = None
    shape: str | None = None
    type_info: dict | None = None
    default: Any | None = None
    required: bool = False


def load_v4_bindings() -> dict[str, dict[str, V4BindingDef]]:
    """Load per-component v4 bindings from data_access.json.

    Returns {component_type: {prop_name: V4BindingDef}} from
    components.X.props entries. Returns empty dict if no v4 bindings.
    """
    data = _load_data_access_config()
    if not data:
        return {}
    components = data.get("components", {})
    result: dict[str, dict[str, V4BindingDef]] = {}
    for comp_name, comp_data in components.items():
        props = comp_data.get("props") if isinstance(comp_data, dict) else None
        if not props:
            continue
        bindings: dict[str, V4BindingDef] = {}
        for prop_name, prop_cfg in props.items():
            bindings[prop_name] = V4BindingDef(
                from_field=prop_cfg.get("from", ""),
                transform=prop_cfg.get("transform", "identity"),
                arity=prop_cfg.get("arity"),
                shape=prop_cfg.get("shape"),
                type_info=prop_cfg.get("type_info"),
                default=prop_cfg.get("default"),
                required=prop_cfg.get("required", False),
            )
        if bindings:
            result[comp_name] = bindings
    return result


# ── BindingIR compilation ────────────────────────────────────────────────


def compile_binding(binding: Binding, framework: str = "react") -> HookBinding | JSExpression:
    """Deterministic TS-safe codegen from BindingIR.

    Lowers DataSourceIR (framework-agnostic) to framework-specific
    expressions:
    - React  → HookBinding (hook variable, hoisted to parent)
    - Vue    → JSExpression (computed property reference) — future

    No string eval, no template expansion, no runtime interpolation.
    Every binding produces a predictable, type-safe result.
    """
    ir = binding.source  # DataSourceIR (framework-agnostic)
    if framework == "react":
        if ir.type == "raw_selector":
            return JSExpression(ir.selector or "undefined")
        hook = _REACT_HOOK_MAP.get(ir.type)
        if hook:
            imp = _HOOK_IMPORT_MAP.get(hook)
            return HookBinding(
                hook_name=hook,
                transform=binding.transform or ir.selector,
                import_stmt=imp,
            )
    raise ValueError(
        f"No framework lowering for DataSourceIR(type={ir.type!r}, "
        f"framework={framework!r})"
    )


# ── Phase 6: Page-level data source loader ────────────────────────────────


def load_page_data_source() -> DataSourceIR | None:
    """Load Page-level DataSourceIR from backend/config/data_access.json (v4 format).

    SSOT: backend/config/data_access.json is the SINGLE source of truth.
    No workspace override, no .opencode lookup.

    In v4, Page's dataSource lives under 'composition.Page.dataSource'.
    Falls back to v3 location 'components.Page.dataSource' for backward compat.

    Returns DataSourceIR with slices populated, or None if missing.
    """
    data = _load_data_access_config()
    if not data:
        return None
    # v4 format: composition.Page.dataSource
    comp = data.get("composition", {})
    page_cfg = comp.get("Page") if comp else None
    ds_raw = page_cfg.get("dataSource") if page_cfg else None
    if ds_raw:
        return _infer_datasource_ir(ds_raw)
    # v3 fallback: components.Page.dataSource
    comps = data.get("components", {})
    page_cfg_v3 = comps.get("Page")
    if page_cfg_v3:
        ds_raw_v3 = page_cfg_v3.get("dataSource")
        if ds_raw_v3:
            return _infer_datasource_ir(ds_raw_v3)
    return None


# ── data_access.json loader (new format) ─────────────────────────────────



def _load_data_access_config() -> dict | None:
    """Load data_access.json from backend/config/data_access.json (global SSOT).

    SSOT: backend/config/data_access.json is the SINGLE source of truth.
    No workspace override, no .opencode lookup. The file is co-located with
    the backend source and defines all Page-level dataSource + slices.

    Returns parsed JSON dict with 'components' key, or None if missing/invalid.
    """
    config_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "data_access.json")
    )
    if not os.path.exists(config_path):
        logger.warning("data_access.json not found at %s", config_path)
        return None
    try:
        with open(config_path) as f:
            data = json.load(f)
        if not isinstance(data, dict) or "components" not in data:
            logger.warning("data_access.json: missing 'components' key — ignoring")
            return None
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("data_access.json: failed to load — %s", e)
        return None





# ── Binding lookup ────────────────────────────────────────────────────────

# Backward compat map: old JSON source.type → DataSourceIR type
_OLD_SOURCE_TYPE_MAP: dict[str, str] = {
    "hook": "dashboard_data",
}


def _infer_datasource_ir(source_raw: dict) -> DataSourceIR:
    """Translate JSON source entry to framework-agnostic DataSourceIR.

    Supports three formats:
      1. v3 semantic: {"type": "dashboard_data", "slices": [...]}
      2. v2 semantic: {"type": "dashboard_data", "selector": "kpiData"}
      3. Old React-specific: {"type": "hook", "name": "useDashboardData"}
    """
    stype = source_raw.get("type", "")
    slices_raw = source_raw.get("slices", [])
    slices = tuple(
        DataSlice(
            component=sr.get("component", ""),
            target_prop=sr.get("targetProp", ""),
            selector=sr.get("selector", ""),
            consumes=tuple(sr.get("consumes", [])),
        )
        for sr in slices_raw
    )
    # New format: semantic type directly
    if stype in _REACT_HOOK_MAP or stype == "raw_selector":
        return DataSourceIR(
            type=stype,
            selector=source_raw.get("selector"),
            slices=slices,
        )
    # Backward compat: old "hook" → "dashboard_data"
    mapped = _OLD_SOURCE_TYPE_MAP.get(stype)
    if mapped:
        return DataSourceIR(
            type=mapped,
            selector=source_raw.get("transform") or source_raw.get("selector"),
            slices=slices,
        )
    return DataSourceIR(type=stype, selector=source_raw.get("selector"), slices=slices)


def _parse_bindings(data: dict | None) -> dict[str, list[Binding]]:
    """Parse the 'bindings' format from data_access.json into Binding objects.

    Returns dict[component_name, list[Binding]].
    """
    result: dict[str, list[Binding]] = {}
    if not data:
        return result

    for comp_name, comp_config in data.get("components", {}).items():
        # Phase 6 v3: skip components with dataSource (not bindings)
        if "dataSource" in comp_config:
            continue
        bindings_list: list[Binding] = []
        for entry in comp_config.get("bindings", []):
            source_raw = entry.get("source", {})
            ir = _infer_datasource_ir(source_raw)
            # Use explicit transform from binding entry, else fallback to selector
            transform = entry.get("transform") or ir.selector
            binding = Binding(
                target_prop=entry.get("targetProp", ""),
                source=ir,
                transform=transform,
                consumes=tuple(entry.get("consumes", [])),
            )
            bindings_list.append(binding)
        result[comp_name] = bindings_list
    return result


def _find_binding(
    component_name: str,
    prop_name: str,
    bindings_map: dict[str, list[Binding]],
) -> Binding | None:
    """Find the first binding matching component + target_prop."""
    comp_bindings = bindings_map.get(component_name, [])
    for b in comp_bindings:
        if b.target_prop == prop_name:
            return b
    return None



