"""BindingResolver — pre-compilation resolution layer.

Resuelve contract params → component props usando data_access.json v4.
Único lugar que conoce contract_params. Compiler recibe solo ResolvedBindings.

Pipeline 5 pasos:
  1. resolve "from" — lookup field en contract_params
  2. validate presence — required=True + no existe → MISSING_CONTRACT_PARAM
  3. validate arity — scalar vs array hint
  4. apply transform — registered transform function
  5. emit — escribir en component_props con provenencia
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.binding.models import BindingDiff, BindingDiffItem, BindingProvenance, ResolvedBindings
from app.graphir.backends.react_backend import JSVariable
from app.signature.prop_mapper import DataSourceIR, load_page_data_source, load_v4_bindings, V4BindingDef

logger = logging.getLogger(__name__)

# ── Transform registry ──────────────────────────────────────────────────
# Transforms are REGISTERABLE by code, not enumerated in JSON.
# The config only references the transform name; the code implements it.
# Add new transforms via register_transform() without touching JSON schema.

_TRANSFORMS: dict[str, Callable[[Any, dict[str, Any]], Any]] = {
    "identity": lambda v, ctx: v,
    "items": lambda v, ctx: [{"label": item, "value": None} for item in (v or [])],
    "wrap": lambda v, ctx: [v] if v is not None else [],
    "value": lambda v, ctx: {"value": v},
    "label": lambda v, ctx: {"label": v},
}


def register_transform(name: str, fn: Callable[[Any, dict[str, Any]], Any]) -> None:
    """Register a new transform function.

    Args:
        name: Transform name (referenced from data_access.json "transform" field)
        fn: Callable(value, contract_params_ctx) → transformed value
    """
    _TRANSFORMS[name] = fn


# ── Dual-write (F0) — comparación entre slices y bindings ───────────────

_MISSING = object()


def _classify_equivalence(value_a: Any, value_b: Any) -> str:
    """Classify a pair of resolved values into one of 3 equivalence classes.

    Classes:
        structural_equivalent: Same slot, different representation
            (e.g. JSVariable ref vs resolved concrete value).
        semantic_equivalent: Same type/shape, identical content.
        divergent: Different values — blocks binding-wins.
    """
    if type(value_a) != type(value_b):
        if isinstance(value_a, JSVariable) or isinstance(value_b, JSVariable):
            return "structural_equivalent"
        return "divergent"
    if isinstance(value_a, list):
        return "semantic_equivalent" if len(value_a) == len(value_b) else "divergent"
    if isinstance(value_a, dict):
        return "semantic_equivalent" if value_a.keys() == value_b.keys() else "divergent"
    return "semantic_equivalent" if value_a == value_b else "divergent"


def _get_declared_prop_names() -> dict[str, set[str]]:
    """Return {component_type: {prop_name, ...}} from v4 binding registry.

    This is the registry-driven diff space: every prop that the binding
    registry declares as resolvable MUST appear in the diff, even if the
    binding couldn't resolve it (e.g. missing contract param).
    """
    bindings = load_v4_bindings()
    return {comp: set(props.keys()) for comp, props in bindings.items()}


def _compute_binding_diff(
    slice_props: dict[str, dict[str, Any]],
    binding_props: dict[str, dict[str, Any]],
    declared_prop_names: dict[str, set[str]] | None = None,
) -> BindingDiff:
    """Compare slice-resolved props vs binding-resolved props.

    Diff space = registry-driven: all props that the binding registry
    declares as resolvable are included, even if the binding couldn't
    resolve them. This ensures we measure "competition of systems" rather
    than "agreement of legacy path".

    Classification when binding declared but couldn't resolve:
    - ``binding_missing``: divergent subset — binding registry declares
      the prop, but the from field didn't match contract_params.
    """
    diff = BindingDiff()
    all_comps = set(slice_props) | set(binding_props)
    if declared_prop_names:
        all_comps |= set(declared_prop_names)
    for comp in sorted(all_comps):
        slice_comp = slice_props.get(comp, {})
        binding_comp = binding_props.get(comp, {})
        all_props = set(slice_comp) | set(binding_comp)
        if declared_prop_names and comp in declared_prop_names:
            all_props |= declared_prop_names[comp]
        for prop in sorted(all_props):
            sv = slice_comp.get(prop, _MISSING)
            bv = binding_comp.get(prop, _MISSING)
            if sv is _MISSING and bv is _MISSING:
                continue
            # Binding declared this prop but couldn't resolve it
            if bv is _MISSING and declared_prop_names and comp in declared_prop_names and prop in declared_prop_names[comp]:
                diff.items.append(BindingDiffItem(
                    component=comp,
                    prop=prop,
                    slice_value=sv,
                    binding_value=None,
                    classification="binding_missing",
                ))
                continue
            # Standard comparison (both resolved something)
            if sv is _MISSING or bv is _MISSING:
                continue
            diff.items.append(BindingDiffItem(
                component=comp,
                prop=prop,
                slice_value=sv,
                binding_value=bv,
                classification=_classify_equivalence(sv, bv),
            ))
    return diff


# ── Type classification (F2.5) — binding eligibility por shape ───────────

PRIMITIVE_TYPES = {"string", "number", "boolean", "enum"}


def is_binding_eligible(type_info: dict | None) -> bool:
    """Deterministic: can binding-wins apply for this prop type?
    
    None (untyped) → True (assumed scalar/UI by default).
    Primitive types → True.
    Arrays of primitives → True.
    Arrays of non-primitives → False.
    Objects → False (CONDITIONAL, not eligible yet).
    """
    return binding_eligibility(type_info) == "YES"


def binding_eligibility(type_info: dict | None) -> str:
    """Returns 'YES', 'NO', or 'CONDITIONAL'.
    
    CONDITIONAL: future-safe for objects that wrap primitives
    (e.g. MetricCard.value → {"value": number}).
    """
    if type_info is None:
        return "YES"
    t = type_info.get("type")
    if t in PRIMITIVE_TYPES:
        return "YES"
    if t == "array":
        items = type_info.get("items")
        return "YES" if items in PRIMITIVE_TYPES or items is None else "NO"
    if t == "object":
        return "CONDITIONAL"
    return "NO"


# ── Errors ──────────────────────────────────────────────────────────────


class MissingContractParam(ValueError):
    """Raised when a required binding's 'from' field is missing in contract_params."""


# ── Resolver ────────────────────────────────────────────────────────────



def _resolve_slices(
    page_ds: Any,
    contract_params: dict[str, Any],
    component_props: dict[str, dict[str, Any]],
    provenance: dict[str, dict[str, str]],
    consumed_params: set[str],
) -> None:
    """Resolve Page data source slices into component_props.

    Each slice is a JSVariable reference (_pageData.<selector>) that the
    renderer uses to inject hook values into child component props.
    """
    if not page_ds:
        return
    for slice_ in getattr(page_ds, "slices", []):
        comp = getattr(slice_, "component", None)
        target_prop = getattr(slice_, "target_prop", None)
        selector = getattr(slice_, "selector", None)
        consumes = getattr(slice_, "consumes", ())
        if not (comp and target_prop and selector):
            continue
        if comp not in component_props:
            component_props[comp] = {}
        if comp not in provenance:
            provenance[comp] = {}
        value = JSVariable(f"_pageData.{selector}")
        component_props[comp][target_prop] = value
        provenance[comp][target_prop] = str(
            BindingProvenance(
                source="slice",
                selector=selector,
                contract_params=list(consumes),
            )
        )
        for cp in consumes:
            if cp in contract_params:
                consumed_params.add(cp)
            elif cp:
                logger.debug(
                    "DRIFT: slice %s.%s consumes '%s' but contract has no such param",
                    comp, target_prop, cp,
                )


def _resolve_v4_bindings(
    contract_params: dict[str, Any],
    component_props: dict[str, dict[str, Any]],
    provenance: dict[str, dict[str, str]],
    consumed_params: set[str],
) -> None:
    """Apply 5-step pipeline for each v4 binding.

    Steps per binding:
      1. resolve "from" — lookup from_field in contract_params
      2. validate presence — if required=True and not found → MISSING_CONTRACT_PARAM
      3. validate arity — scalar vs array hint (warning only)
      4. apply transform — call _TRANSFORMS[name]
      5. emit — write to component_props with provenance
    """
    bindings = load_v4_bindings()
    if not bindings:
        logger.debug("No v4 bindings found in data_access.json")
        return

    for comp_name, prop_bindings in bindings.items():
        if comp_name not in component_props:
            component_props[comp_name] = {}
        if comp_name not in provenance:
            provenance[comp_name] = {}

        for prop_name, binding in prop_bindings.items():
            # F2.5/F3b: type-gated binding ownership.
            # If prop is binding-eligible (scalar/UI type), binding overrides
            # slice. If NOT eligible (complex domain type), slice stays
            # authoritative. This is deterministic per registry schema, not
            # runtime heuristics.
            if prop_name in component_props.get(comp_name, {}):
                if not is_binding_eligible(binding.type_info):
                    continue
                logger.debug(
                    "BINDING_WINS: %s.%s — type_info=%s overrides slice",
                    comp_name, prop_name, binding.type_info,
                )

            # STEP 1: resolve "from"
            raw_value = contract_params.get(binding.from_field)

            # STEP 2: validate presence
            if raw_value is None:
                if binding.required:
                    raise MissingContractParam(
                        f"MISSING_CONTRACT_PARAM:{comp_name}.{prop_name}: "
                        f"required binding from='{binding.from_field}' not in contract_params. "
                        f"Available: {list(contract_params.keys())}"
                    )
                if binding.default is not None:
                    component_props[comp_name][prop_name] = binding.default
                    provenance[comp_name][prop_name] = str(
                        BindingProvenance("default", contract_params=[binding.from_field])
                    )
                    consumed_params.add(binding.from_field)
                    continue
                # not required + no default + not present → skip
                continue

            # STEP 3: validate arity (warning only — not blocking)
            if binding.arity:
                is_array = isinstance(raw_value, list)
                arity_hint = binding.arity
                if "array" in arity_hint and not is_array:
                    logger.debug(
                        "ARITY_MISMATCH: %s.%s expects arity='%s' but got %s (type=%s)",
                        comp_name, prop_name, arity_hint, raw_value, type(raw_value).__name__,
                    )
                elif "scalar" in arity_hint and is_array and arity_hint != "array":
                    logger.debug(
                        "ARITY_MISMATCH: %s.%s expects arity='%s' but got array(len=%d)",
                        comp_name, prop_name, arity_hint, len(raw_value),
                    )

            # STEP 4: apply transform
            transform_fn = _TRANSFORMS.get(binding.transform, _TRANSFORMS["identity"])
            try:
                transformed = transform_fn(raw_value, contract_params)
            except Exception as e:
                logger.warning(
                    "TRANSFORM_FAILED: %s.%s transform='%s' on value=%s: %s",
                    comp_name, prop_name, binding.transform, raw_value, e,
                )
                transformed = raw_value  # fallback to raw value

            # STEP 5: emit
            component_props[comp_name][prop_name] = transformed
            provenance[comp_name][prop_name] = str(
                BindingProvenance(
                    source="v4_binding",
                    selector=binding.transform,
                    contract_params=[binding.from_field],
                )
            )
            consumed_params.add(binding.from_field)


def resolve(
    contract_params: dict[str, Any],
    page_ds_override: DataSourceIR | None = None,
) -> ResolvedBindings:
    """Resolve contract params to component props via data_access.json v4.

    This is the ONLY path for contract → prop translation. No aliases,
    no heuristics, no exact-match fallback. Single source of truth is
    backend/config/data_access.json (global SSOT).

    Pipeline:
       1. Resolve Page slices (composition data flow)
       2. Resolve per-component v4 bindings (5-step pipeline)
       3. Dual-write diff (F1 diagnostic)
       4. Emit unconsumed param warnings (drift detection)

    Args:
        contract_params: Raw params from ConfirmedIntent (e.g. {"metrics": [...]}).
        page_ds_override: Optional DataSourceIR override for testing. When set,
            bypasses the global SSOT and uses this data source directly.

    Returns:
        ResolvedBindings with component_props, provenance, page_data_source.
    """
    component_props: dict[str, dict[str, Any]] = {}
    provenance: dict[str, dict[str, str]] = {}
    consumed_params: set[str] = set()

    # Step 1: Resolve Page slices (composition data flow)
    page_ds = page_ds_override or load_page_data_source()
    _resolve_slices(page_ds, contract_params, component_props, provenance, consumed_params)

    # Step 2: Resolve per-component v4 bindings (5-step pipeline)
    _resolve_v4_bindings(contract_params, component_props, provenance, consumed_params)

    # Step 2b: F1 dual-write — binding-only path for side-by-side comparison.
    _dual_binding_props: dict[str, dict[str, Any]] = {}
    _dual_provenance: dict[str, dict[str, str]] = {}
    _dual_consumed: set[str] = set()
    _resolve_v4_bindings(contract_params, _dual_binding_props, _dual_provenance, _dual_consumed)
    declared = _get_declared_prop_names()
    diff = _compute_binding_diff(component_props, _dual_binding_props, declared)
    for item in diff.items:
        if item.classification == "binding_missing":
            logger.warning(
                "DUAL_WRITE_BINDING_MISSING: %s.%s — binding declares '%s' "
                "but couldn't resolve (from field mismatch). slice=%s",
                item.component, item.prop,
                declared.get(item.component, set()),
                item.slice_value,
            )
        elif item.classification == "divergent":
            logger.warning(
                "DUAL_WRITE_DIVERGENT: %s.%s | slice=%s | binding=%s",
                item.component, item.prop, item.slice_value, item.binding_value,
            )
        elif item.classification == "semantic_equivalent":
            logger.debug(
                "DUAL_WRITE_SEMANTIC_EQ: %s.%s | slice=%s | binding=%s",
                item.component, item.prop, item.slice_value, item.binding_value,
            )
        else:
            logger.debug(
                "DUAL_WRITE_STRUCTURAL_EQ: %s.%s | slice=%s | binding=%s",
                item.component, item.prop, item.slice_value, item.binding_value,
            )
    total_issues = diff.divergent_count + diff.binding_missing_count
    if total_issues > 0:
        logger.warning(
            "DUAL_WRITE: %d divergent + %d binding_missing out of %d total diff props — "
            "binding wins blocked for %d props",
            diff.divergent_count, diff.binding_missing_count,
            diff.total_overlap, total_issues,
        )
    else:
        logger.info(
            "DUAL_WRITE CLEAN: 0 issues out of %d total diff props — binding wins eligible",
            diff.total_overlap,
        )

    # Step 3: Emit unconsumed param warnings (observability, not blocking)
    all_contract_keys = set(contract_params.keys()) if contract_params else set()
    unconsumed_params = all_contract_keys - consumed_params
    for param in sorted(unconsumed_params):
        logger.warning(
            "UNCONSUMED_PARAM: contract param '%s=%s' not consumed by any binding",
            param, contract_params.get(param, "?"),
        )

    return ResolvedBindings(
        component_props=component_props,
        provenance=provenance,
        consumed_params=consumed_params,
        unconsumed_params=unconsumed_params,
        page_data_source=page_ds,
        imports=[],
    )
