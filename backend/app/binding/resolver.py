"""BindingResolver — pre-compilation resolution layer.

Resuelve contract params → component props usando data_access.json v3.
Único lugar que conoce contract_params. Compiler recibe solo ResolvedBindings.
"""

from __future__ import annotations

import logging
from typing import Any

from app.binding.models import BindingProvenance, ResolvedBindings
from app.graphir.backends.react_backend import JSVariable
from app.signature.prop_mapper import load_page_data_source

logger = logging.getLogger(__name__)


def page_ds_has_slice(
    page_data_source: Any,
    component_type: str,
    prop_name: str | None = None,
) -> bool:
    """Check if a Page data source provides data for the given component.

    Moved here from compiler.py. Same semantics: returns True if
    page_data_source has a slice matching component_type (+ optionally prop_name).

    Useful diagnostic signal: "slice exists but binding didn't resolve it"
    is distinguishable from "no slice exists, so reject is correct".
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


def resolve(
    contract_params: dict[str, Any],
) -> ResolvedBindings:
    """Resolve contract params to component props via backend/config/data_access.json (global SSOT).

    This is the ONLY path for contract → prop translation. No aliases,
    no heuristics, no exact-match fallback. Uses the global data_access.json
    from backend/config/ — no workspace dependency, no .opencode lookup.

    Args:
        contract_params: Raw params from ConfirmedIntent (e.g. {"metrics": [...]})

    Returns:
        ResolvedBindings with component_props already populated.
    """
    component_props: dict[str, dict[str, Any]] = {}
    provenance: dict[str, dict[str, str]] = {}
    all_imports: list[str] = []
    consumed_params: set[str] = set()

    # Load page data source from global config (SSOT, no workspace dependency)
    page_ds = load_page_data_source()

    if page_ds:
        # Resolve slices → component props
        for slice_ in page_ds.slices:
            comp = slice_.component
            if comp not in component_props:
                component_props[comp] = {}
                provenance[comp] = {}
            selector = getattr(slice_, "selector", None)
            target_prop = getattr(slice_, "target_prop", None)
            consumes = getattr(slice_, "consumes", ())

            if target_prop and selector:
                # Build JSVariable reference (e.g. _pageData.kpiData)
                value = JSVariable(f"_pageData.{selector}")
                component_props[comp][target_prop] = value
                provenance[comp][target_prop] = str(
                    BindingProvenance(
                        source="slice",
                        selector=selector,
                        contract_params=list(consumes),
                    )
                )
                # Track consumed params
                for cp in consumes:
                    if cp in contract_params:
                        consumed_params.add(cp)
                    elif cp:
                        logger.debug(
                            "DRIFT: slice %s.%s consumes '%s' but contract has no such param",
                            comp, target_prop, cp,
                        )
    else:
        logger.debug("No page data source found in global config")

    # Emit unconsumed param warnings (observability, not blocking)
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
        imports=all_imports,
    )
