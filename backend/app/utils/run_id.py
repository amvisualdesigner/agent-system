import re

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not UUID_PATTERN.match(run_id):
        raise ValueError(f"Invalid run_id: {run_id!r}")
    return run_id
