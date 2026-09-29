"""In-process locks for Session apply/merge serialization (S1-B D6).

Scope: real apply + session_merge share a per-Session lock. Runs without a
Session fall back to a per-run_id key. The backend runs a single uvicorn
process (threadpool for sync endpoints), so a process-local lock registry is
sufficient — no distributed locking in S1-B.

Locks disappear on process restart by design; recovery is file/git based.
"""

from __future__ import annotations

import threading


_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def session_apply_lock(scope_key: str) -> threading.Lock:
    """Get (creating on demand) the lock guarding a Session's physical continuity."""
    with _GUARD:
        lock = _LOCKS.get(scope_key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[scope_key] = lock
        return lock


def keyed_run_scope(session_id: str | None, run_id: str) -> str:
    """Lock scope key: session_id when present, else run_id."""
    return session_id if session_id else run_id