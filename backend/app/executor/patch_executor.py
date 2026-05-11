import os

from app.policy.policy import validate_operation, safe_path
from app.contracts.operations import Action

MAX_FILE_SIZE = 200_000

def apply_operation(op, base_repo):
    ok, reason = validate_operation(op)
    if not ok:
        return {"status": "rejected", "reason": reason, "op": op}

    path = safe_path(op["path"], base_repo)
    content = op.get("diff", "")

    if op["action"] == Action.create:
        return create_file(path, content)
    elif op["action"] == Action.modify:
        return modify_file(path, content)
    elif op["action"] == Action.delete:
        return delete_file(path)
    else:
        return {"status": "error", "reason": "unknown_operation"}

def create_file(path, content):
    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        return {"status": "rejected", "reason": "file_too_large"}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o644)
    return {"status": "created", "path": path}

def modify_file(path, content):
    if not os.path.isfile(path):
        return {"status": "rejected", "reason": "file_not_found", "path": path}
    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        return {"status": "rejected", "reason": "file_too_large"}
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o644)
    return {"status": "modified", "path": path}

def delete_file(path):
    if not os.path.isfile(path):
        return {"status": "rejected", "reason": "file_not_found", "path": path}
    os.remove(path)
    return {"status": "deleted", "path": path}