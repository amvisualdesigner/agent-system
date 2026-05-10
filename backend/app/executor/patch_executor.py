import os
import subprocess
from app.executor.policy import validate_operation, safe_path, BASE_REPO

MAX_FILE_SIZE = 200_000

def apply_operation(op):
    ok, reason = validate_operation(op)

    if not ok:
        return {
            "status": "rejected",
            "reason": reason,
            "op": op
        }

    op_type = op.get("type")
    path = safe_path(op.get("path", ""))

    if op_type == "modify":
        return modify_file(path, op.get("diff", ""))

    if op_type == "create":
        return create_file(path, op.get("diff", ""))

    if op_type == "delete":
        return delete_file(path)

    return {"error": "unknown_operation", "op": op}


def create_file(path, content):
    path = safe_path(path)

    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        return {"error": "file_too_large"}

    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w") as f:
        f.write(content)

    os.chmod(path, 0o644)

    return {"status": "created", "path": path}


def modify_file(path, content):
    path = safe_path(path)

    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}

    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        return {"error": "file_too_large"}

    with open(path, "w") as f:
        f.write(content)

    os.chmod(path, 0o644)

    return {"status": "modified", "path": path}


def delete_file(path):
    path = safe_path(path)

    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}

    os.remove(path)

    return {"status": "deleted", "path": path}


def get_git_diff():
    result = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--ignore-submodules"],
        cwd=BASE_REPO,
        capture_output=True,
        text=True
    )
    return result.stdout