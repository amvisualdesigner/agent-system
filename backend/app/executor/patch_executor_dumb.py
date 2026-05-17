import os
import logging

from app.config.feature_flags import FEATURE_FLAGS

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 200_000


def apply_dumb(op, base_repo):
    path = os.path.normpath(os.path.join(base_repo, op.path))
    if not path.startswith(os.path.realpath(base_repo) + os.sep):
        return {"status": "rejected", "reason": "path_escape", "path": op.path}
    if op.action == "create":
        if len(op.content.encode("utf-8")) > MAX_FILE_SIZE:
            return {"status": "rejected", "reason": "file_too_large"}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(op.content)
        os.chmod(path, 0o644)
        return {"status": "created", "path": path, "source": "executor_dumb"}
    elif op.action == "modify":
        if not os.path.isfile(path):
            return {"status": "rejected", "reason": "file_not_found"}
        with open(path, "w") as f:
            f.write(op.content)
        return {"status": "modified", "path": path}
    elif op.action == "delete":
        if not os.path.isfile(path):
            return {"status": "rejected", "reason": "file_not_found"}
        os.remove(path)
        return {"status": "deleted", "path": path}
    return {"status": "rejected", "reason": "unknown_action"}
