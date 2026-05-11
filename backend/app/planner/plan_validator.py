def validate_plan(plan: dict):
    if not plan.get("steps"):
        return False, "empty_steps"

    for step in plan["steps"]:
        if "path" not in step:
            return False, "missing_path"

        if step["action"] not in ["create", "modify", "delete"]:
            return False, "invalid_action"

        if not step.get("intent"):
            return False, "missing_intent"

    return True, "ok"