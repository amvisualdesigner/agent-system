import os
import re

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not UUID_PATTERN.match(run_id):
        raise ValueError(
            f"Invalid run_id: {run_id!r}. Must be a valid UUID v4 string."
        )
    return run_id


def guard_within(path: str, root: str) -> str:
    real_path = os.path.realpath(path)
    real_root = os.path.realpath(root)
    if real_path != real_root and not real_path.startswith(real_root + os.sep):
        raise PermissionError(
            f"Path escape blocked: {real_path} is not within {real_root}"
        )
    return real_path
