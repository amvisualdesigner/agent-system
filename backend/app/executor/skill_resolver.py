SKILL_REGISTRY = {
    "dashboard.sales_overview": {
        "type": "dashboard",
        "layout": "AnalyticsGrid",
        "nodes": [
            {"type": "KpiRow", "metrics_key": "metrics", "default": []},
            {"type": "Timeseries", "metric_key": "timeseries_metric", "default": "revenue"},
        ],
    },
}


def materialize_nodes(nodes, params):
    result = []
    for n in nodes:
        node = {"type": n["type"]}
        for k, v in n.items():
            if k.endswith("_key"):
                key = v
                value = params.get(key)
                if value is None:
                    value = n.get("default")
                    node["_warning"] = f"missing_param:{key}"
                node[k.replace("_key", "")] = value
            elif k not in ("type", "default"):
                node[k] = v
        result.append(node)
    return result


def resolve_skill(name: str, params: dict) -> dict:
    if name not in SKILL_REGISTRY:
        return {"status": "error", "reason": f"unknown_skill: {name}"}
    skill = SKILL_REGISTRY[name]
    return {
        "type": skill["type"],
        "layout": skill["layout"],
        "nodes": materialize_nodes(skill["nodes"], params),
    }
