import os
import subprocess
from app.executor.policy import validate_operation, safe_path

MAX_FILE_SIZE = 200_000

def apply_operation(op, base_repo):
    ok, reason = validate_operation(op)

    if not ok:
        return {
            "status": "rejected",
            "reason": reason,
            "op": op
        }

    op_type = op.get("type")
    path = safe_path(op["path"], base_repo)

    if op_type == "modify":
        return modify_file(path, op.get("diff", ""), base_repo)

    if op_type == "create":
        return create_file(path, op.get("diff", ""), base_repo)

    if op_type == "delete":
        return delete_file(path, base_repo)

    return {"error": "unknown_operation", "op": op}


def create_file(path, content, base_repo):
    def create_file(path, content, base_repo):
        path = safe_path(path, base_repo)

    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        return {"error": "file_too_large"}

    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w") as f:
        f.write(content)

    os.chmod(path, 0o644)

    return {"status": "created", "path": path}


def modify_file(path, content, base_repo):
    def modify_file(path, content, base_repo):
        path = safe_path(path, base_repo)

    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}

    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        return {"error": "file_too_large"}

    with open(path, "w") as f:
        f.write(content)

    os.chmod(path, 0o644)

    return {"status": "modified", "path": path}


def delete_file(path, base_repo):
    def delete_file(path, base_repo):
        path = safe_path(path, base_repo)

    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}

    os.remove(path)

    return {"status": "deleted", "path": path}


def get_git_diff(workspace):
    subprocess.run(["git", "add", "-A"], cwd=workspace)
    result = subprocess.run(
        ["git", "diff", "--cached"],
        cwd=workspace,
        capture_output=True,
        text=True
    )
    return result.stdout