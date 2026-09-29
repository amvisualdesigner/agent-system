import re

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def validate_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not UUID_PATTERN.match(session_id):
        raise ValueError(
            f"Invalid session_id: {session_id!r}. Must be a valid UUID v4 string."
        )
    return session_id