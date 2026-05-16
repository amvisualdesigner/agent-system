import os

from app.contracts.operations import Action

ALLOWED_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py", ".md", ".json", ".yaml", ".yml", ".txt", ".html", ".css"}
BLOCKED_PATTERNS = [".git", "node_modules", "dist", "build", ".env"]
MAX_FILE_SIZE = 200_000  # 200KB
MAX_OPERATIONS = 10
MAX_DELETES = 3
MAX_SCAFFOLD_OPS_PER_RUN = 3

ALLOWED_TARGETS = {"skill", "component", "layout", "file"}

def safe_path(path: str, base_repo: str):
    # normaliza ruta
    normalized = os.path.normpath(path)

    full_path = os.path.realpath(os.path.join(base_repo, normalized))
    base_path = os.path.realpath(base_repo)

    if not full_path.startswith(base_path + os.sep):
        raise Exception(f"Path escape detected: {path}")

    return full_path

# PLAN POLICY (semantic limits)
# NOTE: PLAN policy is independent of execution node
def validate_plan_policy(plan):
    actions = plan.get("actions", [])

    if len(actions) > MAX_OPERATIONS:
        return False, "too_many_operations"

    delete_ops = [a for a in actions if a.get("type") == "delete"]

    if len(delete_ops) > MAX_DELETES:
        return False, "too_many_deletes"

    scaffold_count = sum(1 for a in actions if a.get("intent") == "scaffold")
    if scaffold_count > MAX_SCAFFOLD_OPS_PER_RUN:
        return False, "scaffold_budget_exceeded"

    return True, "ok"

def is_path_safe(path: str):
    normalized = os.path.normpath(path)
    parts = normalized.split(os.sep)

    forbidden = {".git", "node_modules", "dist", "build", ".env"}

    return not any(p in forbidden for p in parts)

# EXECUTION POLICY (filesystem safety)
def validate_skill_operation(op):
    if not op.get("name"):
        return False, "missing_skill_name"
    params = op.get("params", {})
    if not isinstance(params, dict):
        return False, "invalid_skill_params"
    if len(params) == 0:
        return False, "empty_skill_params"
    return True, "ok"


def validate_operation(op: dict):

    # 0. target válido
    target = op.get("target", "file")
    if target not in ALLOWED_TARGETS:
        return False, "invalid_target"

    # skill operations bypass file checks
    if target == "skill":
        return validate_skill_operation(op)

    # 1. action válida
    try:
        action = Action(op.get("action"))
    except Exception:
        return False, "invalid_operation_action"

    # 2. path obligatorio (solo para target=file)
    if target == "file":
        path = op.get("path")
        if not path:
            return False, "missing_path"

        if not is_path_safe(path):
            return False, "blocked_path"

        ext = os.path.splitext(path)[1]
        if ext not in ALLOWED_EXTENSIONS:
            return False, "extension_not_allowed"

    # 3. tamaño
    content = op.get("content") or op.get("proposed_content")
    if content and len(content) > MAX_FILE_SIZE:
        return False, "file_too_large"

    # 4. intent validation for create (structural operation)
    if action == Action.create:
        intent = op.get("intent", "")
        if intent and intent not in {"empty", "scaffold", "full"}:
            return False, "invalid_intent"

    # 5. diff obligatorio solo para modify (patch operation)
    if action == Action.modify and not op.get("diff"):
        return False, "missing_diff"

    return True, "ok"