from app.contracts.operations import Action

def validate_plan(plan: dict):
    steps = plan.get("steps", [])

    if not steps:
        return False, "empty_steps"

    for step in steps:

        if "path" not in step:
            return False, "missing_path"

        # 🔥 canonical validation via Enum
        try:
            Action(step["action"])
        except Exception:
            return False, "invalid_action"

        if not step.get("intent"):
            return False, "missing_intent"

    return True, "ok"