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

from app.binding.models import BindingProvenance, ResolvedBindings
from app.graphir.backends.react_backend import JSVariable
from app.signature.prop_mapper import load_page_data_source, load_v4_bindings, V4BindingDef

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


# ── Errors ──────────────────────────────────────────────────────────────


class MissingContractParam(ValueError):
    """Raised when a required binding's 'from' field is missing in contract_params."""


# ── Resolver ────────────────────────────────────────────────────────────


def page_ds_has_slice(
    page_data_source: Any,
    component_type: str,
    prop_name: str | None = None,
) -> bool:
    """Check if a Page data source provides data for the given component.

    Returns True if page_data_source has a slice matching component_type
    (+ optionally prop_name). Diagnostic signal only — not used for resolution.
    """
    if not page_data_source:
        return False
    for slice_ in getattr(page_data_source, "slices", []):
        if getattr(slice_, "component", None) == component_type:
            if prop_name is None:
                return True
            if getattr(slice_, "target_prop", None) == prop_name:
                return True
    return False


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
            # Skip if already resolved via Page data source (composition priority)
            if prop_name in component_props.get(comp_name, {}):
                continue

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
    workspace: str | None = None,
) -> ResolvedBindings:
    """Resolve contract params to component props via data_access.json v4.

    This is the ONLY path for contract → prop translation. No aliases,
    no heuristics, no exact-match fallback. Uses data_access.json v4 format
    from backend/config/ (global SSOT, no workspace dependency).

    Pipeline:
      1. Resolve Page slices (composition data flow)
      2. Resolve per-component v4 bindings (5-step pipeline)
      3. Emit async warnings for unconsumed params (drift detection)

    Args:
        contract_params: Raw params from ConfirmedIntent (e.g. {"metrics": [...]})
        workspace: Optional workspace path (legacy backward compat for Phase 6 tests).

    Returns:
        ResolvedBindings with component_props, provenance, page_data_source.
    """
    component_props: dict[str, dict[str, Any]] = {}
    provenance: dict[str, dict[str, str]] = {}
    consumed_params: set[str] = set()

    # Step 1: Resolve Page slices (composition data flow)
    if workspace:
        from app.signature.prop_mapper import load_workspace_data_source
        page_ds = load_workspace_data_source(workspace)
    else:
        from app.signature.prop_mapper import load_page_data_source
        page_ds = load_page_data_source()
    _resolve_slices(page_ds, contract_params, component_props, provenance, consumed_params)

    # Step 2: Resolve per-component v4 bindings (direct, standalone)
    # Skip v4 bindings when loading from legacy workspace (v3 config, no v4 format)
    if not workspace:
        _resolve_v4_bindings(contract_params, component_props, provenance, consumed_params)

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
