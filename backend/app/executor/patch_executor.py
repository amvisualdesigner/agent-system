import os
import subprocess
from app.executor.policy import validate_operation, safe_path, BASE_REPO


def checkpoint_repo():
    subprocess.run(["git", "add", "-A"], cwd=BASE_REPO)
    subprocess.run(["git", "commit", "-m", "agent checkpoint"], cwd=BASE_REPO)


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
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w") as f:
        f.write(content)

    os.chmod(path, 0o644)

    return {"status": "created", "path": path}


def modify_file(path, content):
    if not os.path.exists(path):
        return {"error": "file_not_found", "path": path}

    with open(path, "w") as f:
        f.write(content)

    os.chmod(path, 0o644)

    return {"status": "modified", "path": path}


def delete_file(path):
    if os.path.exists(path):
        os.remove(path)
        return {"status": "deleted", "path": path}

    return {"error": "file_not_found", "path": path}


def get_git_diff():
    result = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--ignore-submodules"],
        cwd=BASE_REPO,
        capture_output=True,
        text=True
    )
    return result.stdout