"""Runtime state store for the Session lifecycle.

Session is lifecycle bookkeeping ONLY (never semantic authority). Reuses the
file-based JSON persistence mechanism of run_state.py (same STATE_DIR, same
json.dump + fsync contract) under a dedicated `sessions/` subdirectory.

File layout:
    {STATE_DIR}/sessions/{session_id}.json
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.session.models import (
    SessionRecord,
    SessionStatus,
    now_iso,
    validate_session_transition,
)
from app.utils.path_guard import guard_within

logger = logging.getLogger(__name__)

STATE_DIR = os.environ.get("STATE_DIR") or os.environ.get("RUNS_DIR") or os.path.join(
    os.path.dirname(__file__), "..", "..", "run_states"
)

SESSIONS_DIR = os.path.join(STATE_DIR, "sessions")


def _ensure_dir() -> None:
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def _path(session_id: str) -> str:
    path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
    guard_within(path, SESSIONS_DIR)
    return path


def create_session_record(record: SessionRecord) -> SessionRecord:
    """Persist a new ACTIVE Session record. Overwrite is refused."""
    _ensure_dir()
    existing = load_session(record.session_id)
    if existing is not None:
        raise ValueError(f"Session already exists: {record.session_id}")
    payload = record.to_dict()
    if not payload.get("created_at"):
        payload["created_at"] = now_iso()
    payload["updated_at"] = payload["created_at"]
    dst = _path(record.session_id)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    logger.info("[session_id=%s] record created (status=%s)", record.session_id, record.status.value)
    return load_session(record.session_id) or record


def save_session(session_id: str, **updates) -> SessionRecord | None:
    """Merge updates into the persisted Session record."""
    record = load_session(session_id)
    if record is None:
        return None
    for key, value in updates.items():
        if hasattr(record, key):
            setattr(record, key, value)
    record.updated_at = now_iso()
    payload = record.to_dict()
    dst = _path(session_id)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    logger.debug("[session_id=%s] state saved (status=%s)", session_id, record.status.value)
    return record


def _load_raw(session_id: str) -> Optional[dict]:
    path = _path(session_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("[session_id=%s] failed to load state: %s", session_id, str(e))
        return None


def load_session(session_id: str) -> Optional[SessionRecord]:
    """Load a Session record from disk. Returns None if not found."""
    raw = _load_raw(session_id)
    if raw is None:
        return None
    try:
        return SessionRecord.from_dict(raw)
    except Exception as e:
        logger.warning("[session_id=%s] failed to parse state: %s", session_id, str(e))
        return None


def load_session_dict(session_id: str) -> Optional[dict]:
    """Load a Session record as a plain dict (no parsing)."""
    return _load_raw(session_id)


def list_session_records() -> list[SessionRecord]:
    """List all persisted Session records (sorted by created_at desc)."""
    _ensure_dir()
    records: list[SessionRecord] = []
    for name in os.listdir(SESSIONS_DIR):
        if not name.endswith(".json"):
            continue
        session_id = name[:-5]
        record = load_session(session_id)
        if record is not None:
            records.append(record)
    records.sort(key=lambda r: r.created_at or "", reverse=True)
    return records


def transition_session(session_id: str, target: SessionStatus) -> SessionRecord:
    """Validate and persist a Session status transition.

    Returns the updated record.

    Raises:
        ValueError: if the Session does not exist or the transition is invalid.
    """
    record = load_session(session_id)
    if record is None:
        raise ValueError(f"Session not found: {session_id}")
    validate_session_transition(record.status, target)
    updated = save_session(session_id, status=target)
    logger.info("[session_id=%s] status %s → %s", session_id, record.status.value, target.value)
    return updated or record