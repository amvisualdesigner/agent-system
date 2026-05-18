"""Normalize semantic UI plans into structurally valid component graphs.

Pure function. No side effects. No mutation of inputs.
Runs after contract resolution, before tree construction.
"""

from copy import deepcopy

COMPONENT_MAP = {
    "RevenueChart": "Timeseries",
    "RevenueTable": "KpiRow",
}


def normalize_plan(plan: dict, component_registry: set[str]) -> dict:
    """Replace semantic-only components with valid structural equivalents.

    - Unknown components in COMPONENT_MAP are remapped and get default props.
    - Components not in registry AND not in COMPONENT_MAP are dropped.
    - Valid known components pass through unchanged.
    - Returns a new dict; input is never mutated.
    """
    normalized = deepcopy(plan)

    actions = normalized.get("actions", [])
    new_actions: list[dict] = []

    for action in actions:
        component = action.get("type") or action.get("component")

        if component in component_registry:
            new_actions.append(action)
            continue

        if component in COMPONENT_MAP:
            mapped = COMPONENT_MAP[component]
            action["type"] = mapped
            action["component"] = mapped
            if mapped == "Timeseries":
                action.setdefault("props", {})
                action["props"]["metric"] = "revenue"
            new_actions.append(action)
            continue

        # Unknown semantic intent — drop safely
        continue

    normalized["actions"] = new_actions
    return normalized
