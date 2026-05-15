from app.contracts.operations import Action

def validate_plan(plan: dict):
    actions = plan.get("actions", [])

    if not actions:
        return False, "empty_actions"

    for a in actions:

        if "file_path" not in a:
            return False, "missing_path"

        if not a.get("description"):
            return False, "missing_description"

    return True, "ok"