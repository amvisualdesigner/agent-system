import os

BASE_REPO = "/opt/agent-repos/agent-test-repo"

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

def safe_path(path: str):
    full_path = os.path.realpath(os.path.join(BASE_REPO, path))
    base_path = os.path.realpath(BASE_REPO)

    if not full_path.startswith(base_path + os.sep):
        raise Exception(f"Path escape detected: {path}")

    return full_path


def validate_operation(op: dict):
    # 1. tipo permitido
    if op.get("type") not in ALLOWED_OPS:
        return False, "invalid_operation_type"

    # 2. path obligatorio
    if "path" not in op:
        return False, "missing_path"

    # 3. validación de path seguro
    try:
        safe_path(op["path"])
    except Exception as e:
        return False, str(e)

    # 🔥 3.1 BLOQUEO DE PATRONES PELIGROSOS (AQUÍ)
    for blocked in BLOCKED_PATTERNS:
        if blocked in op["path"]:
            return False, "blocked_path"

    # 🔥 3.2 EXTENSIÓN PERMITIDA (AQUÍ)
    ext = os.path.splitext(op["path"])[1]
    if ext not in ALLOWED_EXTENSIONS:
        return False, "extension_not_allowed"

    # 4. diff obligatorio en create/modify
    if op["type"] in ["create", "modify"] and not op.get("diff"):
        return False, "missing_diff"

    return True, "ok"