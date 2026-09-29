from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


__all__ = [
    "SessionStatus",
    "SessionRecord",
    "validate_session_transition",
    "now_iso",
]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


class SessionStatus(str, Enum):
    """Session lifecycle — persisted states.

    MERGE_READY is DERIVED at read-time (merge preconditions); it is never a
    transition target and never persisted.
    """
    ACTIVE = "active"
    MERGED = "merged"
    CONFLICT = "conflict"
    FAILED = "failed"


_VALID_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.ACTIVE: {SessionStatus.MERGED, SessionStatus.CONFLICT, SessionStatus.FAILED},
    SessionStatus.CONFLICT: {SessionStatus.MERGED, SessionStatus.CONFLICT, SessionStatus.FAILED},
    SessionStatus.MERGED: set(),   # terminal
    SessionStatus.FAILED: set(),   # terminal
}


def validate_session_transition(current: SessionStatus, target: SessionStatus) -> None:
    """Raise ValueError if the Session transition is invalid.

    Self-transitions (same → same) are always allowed for idempotency
    (same contract as RunPhase.validate_transition).
    """
    if current == target:
        return
    allowed = _VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        allowed_str = ", ".join(p.value for p in allowed) if allowed else "none"
        raise ValueError(
            f"Cannot transition Session from '{current.value}' to '{target.value}'. "
            f"Allowed transitions from '{current.value}': {allowed_str}"
        )


@dataclass
class SessionRecord:
    """Persisted lifecycle-only state of a Session.

    Lifecycle bookkeeping + physical identity. Explicitly NOT a semantic
    authority: no intent / plan / preview / FileOps / interpretation.
    """
    session_id: str
    base_branch: str
    branch: str
    workspace: str
    status: SessionStatus = SessionStatus.ACTIVE
    run_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    merged_commit: str | None = None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "base_branch": self.base_branch,
            "branch": self.branch,
            "workspace": self.workspace,
            "status": self.status.value,
            "run_ids": list(self.run_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "merged_commit": self.merged_commit,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionRecord":
        status = d.get("status", SessionStatus.ACTIVE.value)
        return cls(
            session_id=d["session_id"],
            base_branch=d.get("base_branch", "master"),
            branch=d.get("branch", ""),
            workspace=d.get("workspace", ""),
            status=status if isinstance(status, SessionStatus) else SessionStatus(status),
            run_ids=list(d.get("run_ids", [])),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            merged_commit=d.get("merged_commit"),
        )