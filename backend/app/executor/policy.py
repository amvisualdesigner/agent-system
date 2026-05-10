import os

ALLOWED_OPS = {"create", "modify", "delete"}

ALLOWED_EXTENSIONS = {
    ".ts",
    ".js",
    ".py",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".txt"
}

BLOCKED_PATTERNS = [
    ".git",
    "node_modules",
    "dist",
    "build",
    ".env"
]

MAX_FILE_SIZE = 200_000  # 200KB por archivo

def safe_path(path: str, base_repo: str):
    full_path = os.path.realpath(os.path.join(base_repo, path))
    base_path = os.path.realpath(base_repo)

    if not full_path.startswith(base_path + os.sep):
        raise Exception(f"Path escape detected: {path}")

    return full_path


def validate_operation(op: dict):
    if op.get("type") not in ALLOWED_OPS:
        return False, "invalid_operation_type"

    if "path" not in op:
        return False, "missing_path"

    for blocked in BLOCKED_PATTERNS:
        if blocked in op["path"]:
            return False, "blocked_path"

    ext = os.path.splitext(op["path"])[1]
    if ext not in ALLOWED_EXTENSIONS:
        return False, "extension_not_allowed"

    if op["type"] in ["create", "modify"] and not op.get("diff"):
        return False, "missing_diff"

    return True, "ok"