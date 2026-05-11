import os

from app.contracts.operations import Action

ALLOWED_EXTENSIONS = {".ts", ".js", ".py", ".md", ".json", ".yaml", ".yml", ".txt"}
BLOCKED_PATTERNS = [".git", "node_modules", "dist", "build", ".env"]
MAX_FILE_SIZE = 200_000  # 200KB
MAX_OPERATIONS = 20
MAX_DELETES = 3

def safe_path(path: str, base_repo: str):
    full_path = os.path.realpath(os.path.join(base_repo, path))
    base_path = os.path.realpath(base_repo)
    if not full_path.startswith(base_path + os.sep):
        raise Exception(f"Path escape detected: {path}")
    return full_path

# PLAN POLICY (semantic limits)
# NOTE: PLAN policy is independent of execution node
def validate_plan_policy(plan):
    steps = plan.get("steps", [])

    if len(steps) > MAX_OPERATIONS:
        return False, "too_many_operations"

    delete_ops = [s for s in steps if s.get("action") == Action.delete]

    if len(delete_ops) > MAX_DELETES:
        return False, "too_many_deletes"

    return True, "ok"

# EXECUTION POLICY (filesystem safety)
def validate_operation(op: dict):

    # 1. action válida (SOURCE OF TRUTH = Enum)
    try:
        action = Action(op.get("action"))
    except Exception:
        return False, "invalid_operation_action"

    # 2. path obligatorio
    if "path" not in op:
        return False, "missing_path"

    # 3. bloqueo de rutas peligrosas
    for blocked in BLOCKED_PATTERNS:
        if blocked in op["path"]:
            return False, "blocked_path"

    # 4. extensión
    ext = os.path.splitext(op["path"])[1]

    if ext not in ALLOWED_EXTENSIONS:
        return False, "extension_not_allowed"

    # 5. diff obligatorio según acción
    if action in {Action.create, Action.modify} and not op.get("diff"):
        return False, "missing_diff"

    return True, "ok"