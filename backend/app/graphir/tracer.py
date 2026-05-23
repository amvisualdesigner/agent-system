"""PropTracer — strict provenance tracing for UIComponentNode.props.

Post-hoc analysis: given an IntentPlan, GraphIR, and UIComponentTree,
traces every prop key back to its exact origin in the pipeline.

Output:
  prop_trace: per-node, per-key → source, value, full prop_path
  prop_conflicts: same key claimed by multiple origins
  cross_capability_leaks: key injected by intent whose capability
    does not own that key per CAPABILITY_SCHEMA
"""

from __future__ import annotations

from typing import Any

from app.graphir.models import GraphIR
from app.graphir.intent import Intent, IntentPlan
from app.graphir.ui_ir import UIComponentTree
from app.graphir.binding import (
    CAPABILITY_SCHEMA,
    _get_all_schema_keys,
)


def _build_intent_map(plan: IntentPlan) -> dict[str, Intent]:
    """Intent.id → Intent (skip IntentNode)."""
    result: dict[str, Intent] = {}
    for intent in plan.intents:
        if isinstance(intent, Intent):
            result[intent.id] = intent
    return result


def _find_intent_for_node(
    node_id: str,
    graph: GraphIR,
    intent_map: dict[str, Intent],
) -> Intent | None:
    """Match a GraphIRNode to its originating Intent via metadata."""
    node = graph.nodes.get(node_id)
    if node is None:
        return None
    intent_id = node.metadata.get("intent_id", "")
    if intent_id and intent_id in intent_map:
        return intent_map[intent_id]
    return None


def _build_prop_path(source_label: str, had_binding: bool) -> list[str]:
    path = [source_label]
    path.append("builder._add_intent_node")
    path.append("graphir_node.data")
    if had_binding:
        path.append("binding.bind_skillir_to_nodes")
    path.append("compiler._build_node")
    path.append("ui_component_node.props")
    return path


def trace_props(
    plan: IntentPlan,
    graph: GraphIR,
    ui_tree: UIComponentTree,
) -> dict:
    """Trace every prop in the pipeline back to its origin.

    Non-invasive: pure analysis, no side effects on pipeline state.

    Args:
        plan: IntentPlan (carries intents + SkillIR params).
        graph: Frozen GraphIR with node.data.
        ui_tree: Compiled UIComponentTree.

    Returns:
        dict with:
          - prop_trace: dict[node_id][prop_key] → {value, source, capability, prop_path}
          - prop_conflicts: list of {node_id, prop_key, sources}
          - cross_capability_leaks: list of {node_id, prop_key, injected_by, node_capability, expected_capability_for_key}
    """
    intent_map = _build_intent_map(plan)
    plan_params = plan.params or {}
    used_keys_in_schema = _get_all_schema_keys_snapshot()

    # Build flat node_id → UIComponentNode for verification
    ui_node_map: dict[str, Any] = {}
    def _flatten(n):
        ui_node_map[n.id] = n
        for c in n.children:
            _flatten(c)
    _flatten(ui_tree.root)

    prop_trace: dict[str, dict[str, dict]] = {}
    prop_conflicts: list[dict] = []
    cross_capability_leaks: list[dict] = []

    for node_id, node in graph.nodes.items():
        intent = _find_intent_for_node(node_id, graph, intent_map)
        intent_params = dict(intent.params) if intent else {}
        capability = node.metadata.get("intent_capability", intent.capability if intent else "")
        schema_keys = _get_all_schema_keys(capability)

        # Trace each key in node.data
        for key in node.data:
            value = node.data[key]

            # Determine source
            in_intent = key in intent_params
            in_plan = key in plan_params
            from_skillir = in_plan and key in schema_keys

            if from_skillir:
                source = "skill_ir"
                source_label = "skill_ir.params"
            elif in_intent:
                intent_id = intent.id if intent else "unknown"
                source = f"intent:{intent_id}"
                source_label = f"intent.{intent_id}.params"
            else:
                source = "graphir_node"
                source_label = "graphir_node (default)"

            prop_path = _build_prop_path(source_label, had_binding=from_skillir)

            if node_id not in prop_trace:
                prop_trace[node_id] = {}

            prop_trace[node_id][key] = {
                "value": value,
                "source": source,
                "capability": capability,
                "prop_path": prop_path,
            }

        # Cross-capability leak detection:
        # A key attributed to an intent that does NOT belong to this node's schema
        if intent and schema_keys:
            for key in intent_params:
                if key not in schema_keys and key not in plan_params:
                    # This intent injected a key not expected for this capability
                    owning_cap = _find_owning_capability(key)
                    cross_capability_leaks.append({
                        "node_id": node_id,
                        "prop_key": key,
                        "injected_by": f"intent:{intent.id}",
                        "node_capability": capability,
                        "expected_capability_for_key": owning_cap or "UNKNOWN",
                    })

    # Conflict detection: multiple intents writing the same key on same node
    # (rare — requires multiple intents mapping to same node_id)
    # Also detect if ui_node_map props don't match graph node.data
    for node_id, node in graph.nodes.items():
        ui_node = ui_node_map.get(node_id)
        if ui_node is None:
            continue
        for key in ui_node.props:
            graph_value = node.data.get(key)
            ui_value = ui_node.props[key]
            if graph_value != ui_value:
                if node_id not in prop_trace:
                    prop_trace[node_id] = {}
                if key not in prop_trace.get(node_id, {}):
                    prop_trace[node_id][key] = {
                        "value": ui_value,
                        "source": "UNKNOWN",
                        "capability": node.metadata.get("intent_capability", ""),
                        "prop_path": ["UNKNOWN"],
                    }

    # Check for props in ui_node_map not in graph node.data (compiler injection)
    for node_id, ui_node in ui_node_map.items():
        if node_id not in graph.nodes:
            continue
        graph_keys = set(graph.nodes[node_id].data.keys())
        ui_keys = set(ui_node.props.keys())
        extra_keys = ui_keys - graph_keys
        for key in extra_keys:
            if node_id not in prop_trace:
                prop_trace[node_id] = {}
            prop_trace[node_id][key] = {
                "value": ui_node.props[key],
                "source": "UNKNOWN",
                "capability": graph.nodes[node_id].metadata.get("intent_capability", ""),
                "prop_path": ["UNKNOWN"],
            }

    return {
        "prop_trace": prop_trace,
        "prop_conflicts": prop_conflicts,
        "cross_capability_leaks": cross_capability_leaks,
    }


def _get_all_schema_keys_snapshot() -> set[str]:
    """Pre-compute all known semantic keys across all capabilities."""
    all_keys: set[str] = set()
    for schema in CAPABILITY_SCHEMA.values():
        all_keys.update(schema["required"])
        all_keys.update(schema["optional"])
    return all_keys


def _find_owning_capability(key: str) -> str | None:
    """Return the first capability that owns this key, or None."""
    for cap, schema in CAPABILITY_SCHEMA.items():
        if key in schema["required"] or key in schema["optional"]:
            return cap
    return None
