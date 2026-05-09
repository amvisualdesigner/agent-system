import os

BASE_REPO = "/opt/agent-repos/agent-test-repo"

ALLOWED_OPS = {"create", "modify", "delete"}

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

    # 3. normalización de path
    try:
        safe_path(op["path"])
    except Exception as e:
        return False, str(e)

    # 4. diff obligatorio en create/modify
    if op["type"] in ["create", "modify"] and not op.get("diff"):
        return False, "missing_diff"

    return True, "ok"