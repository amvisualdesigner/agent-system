"""SkillIR Binding Contract Layer.

Enforces that SkillIR params are bound to GraphIR nodes before rendering.
SkillIR is the authoritative semantic contract — if it specifies params
(columns, metrics, etc.), those MUST appear in the corresponding node's data.

Architecture:
  - CAPABILITY_SCHEMA defines required/optional params per capability
  - bind_skillir_to_nodes() merges plan.params into node.data per schema
  - validate_binding() fails fast if required keys are missing after merge

This is NOT a "merge utility". It is a contract enforcement layer.
The binder is the semantic ownership boundary.
"""

from __future__ import annotations

from typing import Any

from app.graphir.intent import IntentPlan
from app.graphir.models import GraphIRDraft, GraphIRNode


class SkillIRBindingError(ValueError):
    """Raised when SkillIR params cannot be bound to a node's data.

    This is a STRUCTURAL failure: the semantic contract cannot be
    satisfied because required params are missing or empty after binding.
    """
    pass


# ═══════════════════════════════════════════════════════════════════
# Capability Schema — authoritative param contracts per capability
# ═══════════════════════════════════════════════════════════════════
# Each entry defines:
#   required: params that MUST be non-empty in node.data after binding
#   optional: params that MAY appear, bound if present in SkillIR
#
# This is the SINGLE source of truth for what params each capability
# expects. Both the binder AND the renderer should reference this.
# ═══════════════════════════════════════════════════════════════════

CAPABILITY_SCHEMA: dict[str, dict[str, list[str]]] = {
    "presentation.table": {
        "required": ["columns"],
        "optional": ["table_data", "metrics", "dimensions", "top_k"],
    },
    "presentation.kpi_row": {
        "required": ["metrics"],
        "optional": [],
    },
    "presentation.timeseries": {
        "required": ["metric"],
        "optional": ["time_granularity", "group_by"],
    },
    "presentation.chart.bar": {
        "required": ["metrics"],
        "optional": ["categories", "top_k"],
    },
    "presentation.metric_card": {
        "required": ["metric"],
        "optional": [],
    },
    "presentation.filter_panel": {
        "required": [],
        "optional": ["filters"],
    },
    "presentation.embed": {
        "required": ["src"],
        "optional": ["title"],
    },
    # Legacy capabilities (IntentType-based)
    "display.kpi_row": {
        "required": ["metrics"],
        "optional": [],
    },
    "display.timeseries": {
        "required": ["metric"],
        "optional": ["time_granularity", "group_by"],
    },
    "display.analytics_table": {
        "required": ["columns"],
        "optional": ["table_data"],
    },
    "display.filter_panel": {
        "required": [],
        "optional": ["filters"],
    },
}


# ═══════════════════════════════════════════════════════════════════
# Binding policy
# ═══════════════════════════════════════════════════════════════════
# 1. For each node with a recognized capability, merge matching keys
#    from plan.params (SkillIR) into node.data.
# 2. SkillIR values take PRIORITY over existing node.data values for
#    schema-defined keys. Non-schema keys in node.data are preserved.
# 3. If a node has no recognized capability, no binding is performed.
#    (The node keeps its data as-is.)
# ═══════════════════════════════════════════════════════════════════


def _get_all_schema_keys(capability: str) -> set[str]:
    """Return union of required + optional keys for a capability."""
    schema = CAPABILITY_SCHEMA.get(capability)
    if schema is None:
        return set()
    return set(schema["required"]) | set(schema["optional"])


def bind_skillir_to_nodes(draft: GraphIRDraft, plan: IntentPlan) -> None:
    """Bind SkillIR contract params into node.data per capability schema.

    This is a Phase 2.5 step in GraphIR construction: after node
    materialization (Phase 2) and before root election (Phase 3).

    Args:
        draft: Mutable GraphIR draft with nodes registered.
        plan: IntentPlan carrying SkillIR params.

    Raises:
        SkillIRBindingError: if a required schema key is missing
            from both SkillIR and node.data after merge.
    """
    if not plan.params:
        return

    for node_id, node in list(draft.nodes.items()):
        capability = node.metadata.get("intent_capability", "")
        schema_keys = _get_all_schema_keys(capability)
        if not schema_keys:
            continue

        merged_data = dict(node.data)
        changed = False

        for key in schema_keys:
            if key in plan.params:
                bound_value = plan.params[key]
                existing = merged_data.get(key)
                if existing != bound_value:
                    merged_data[key] = bound_value
                    changed = True

        if changed:
            draft.nodes[node_id] = GraphIRNode(
                id=node.id,
                type=node.type,
                data=merged_data,
                metadata=node.metadata,
            )


def validate_binding(draft: GraphIRDraft, plan: IntentPlan) -> None:
    """Validate that all required schema keys are bound after merge.

    ONLY enforces when SkillIR params exist (plan.params non-empty).
    If no SkillIR contract is present, validation is a no-op:
    the builder may still produce valid IR from decomposition alone.

    Must be called AFTER bind_skillir_to_nodes().

    Args:
        draft: Mutable GraphIR draft with binding applied.
        plan: IntentPlan carrying SkillIR params.

    Raises:
        SkillIRBindingError: if SkillIR exists but required schema keys
            are missing or empty in node.data after binding.
    """
    if not plan.params:
        return

    for node_id, node in draft.nodes.items():
        capability = node.metadata.get("intent_capability", "")
        schema = CAPABILITY_SCHEMA.get(capability)
        if schema is None:
            continue

        missing: list[str] = []
        for key in schema["required"]:
            value = node.data.get(key)
            if value is None or (isinstance(value, (list, dict, str)) and not value):
                missing.append(key)

        if missing:
            capability_label = capability or node.type
            raise SkillIRBindingError(
                f"SkillIR binding failed for node '{node_id}' "
                f"(capability='{capability_label}'): "
                f"required params {missing} are missing or empty "
                f"in node.data after binding. "
                f"SkillIR must provide values for: {schema['required']}. "
                f"Plan params available: {list(plan.params.keys())}."
            )
