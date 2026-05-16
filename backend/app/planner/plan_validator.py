from app.policy.policy import MAX_SCAFFOLD_OPS_PER_RUN


def prune_scaffold(plan: dict):
    actions = plan.get("actions", [])
    scaffold_ops = [a for a in actions if a.get("intent") == "scaffold"]
    if len(scaffold_ops) > MAX_SCAFFOLD_OPS_PER_RUN:
        non_scaffold = [a for a in actions if a.get("intent") != "scaffold"]
        plan["actions"] = non_scaffold + scaffold_ops[:MAX_SCAFFOLD_OPS_PER_RUN]
        plan["pruned"] = True
        plan["pruned_reason"] = "scaffold_budget_exceeded"
        plan["pruned_count"] = len(scaffold_ops) - MAX_SCAFFOLD_OPS_PER_RUN


def validate_plan(plan: dict):
    actions = plan.get("actions", [])

    if not actions:
        return False, "empty_actions"

    for a in actions:
        if "type" not in a:
            return False, "missing_type"

    return True, "ok"