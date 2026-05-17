import os

from app.contracts.operations import Action

ALLOWED_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py", ".md", ".json", ".yaml", ".yml", ".txt", ".html", ".css"}
BLOCKED_PATTERNS = [".git", "node_modules", "dist", "build", ".env"]
MAX_FILE_SIZE = 200_000
MAX_OPERATIONS = 20

ALLOWED_TARGETS = {"file"}


def safe_path(path: str, base_repo: str):
    normalized = os.path.normpath(path)
    full_path = os.path.realpath(os.path.join(base_repo, normalized))
    base_path = os.path.realpath(base_repo)
    if not full_path.startswith(base_path + os.sep):
        raise Exception(f"Path escape detected: {path}")
    return full_path


def validate_plan_policy(plan):
    actions = plan.get("actions", [])
    if len(actions) > MAX_OPERATIONS:
        return False, "too_many_operations"
    return True, "ok"


def is_path_safe(path: str):
    normalized = os.path.normpath(path)
    parts = normalized.split(os.sep)
    forbidden = {".git", "node_modules", "dist", "build", ".env"}
    return not any(p in forbidden for p in parts)


def validate_operation(op: dict):
    target = op.get("target", "file")
    if target not in ALLOWED_TARGETS:
        return False, "invalid_target"

    try:
        action = Action(op.get("action"))
    except Exception:
        return False, "invalid_operation_action"

    if target == "file":
        path = op.get("path")
        if not path:
            return False, "missing_path"
        if not is_path_safe(path):
            return False, "blocked_path"
        ext = os.path.splitext(path)[1]
        if ext not in ALLOWED_EXTENSIONS:
            return False, "extension_not_allowed"

    content = op.get("content") or op.get("proposed_content")
    if content and len(content) > MAX_FILE_SIZE:
        return False, "file_too_large"

    if action == Action.create:
        intent = op.get("intent", "")
        if intent and intent not in {"empty", "scaffold", "full"}:
            return False, "invalid_intent"

    if action == Action.modify and not op.get("diff"):
        return False, "missing_diff"

    return True, "ok"
