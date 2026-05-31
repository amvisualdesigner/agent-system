"""Runtime state store for interpret → confirm → apply flow.

Keyed by run_id. File-based JSON persistence for crash recovery.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.intent.models import RunPhase, RunState, validate_transition

logger = logging.getLogger(__name__)

STATE_DIR = os.environ.get("RUNS_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "run_states"))


def _ensure_dir() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


def _path(run_id: str) -> str:
    return os.path.join(STATE_DIR, f"{run_id}.json")


def save_run_state(run_id: str, state: dict) -> str:
    """Persist run state to disk. Merges with existing state."""
    _ensure_dir()
    existing = load_run_state(run_id) or {}
    existing.update(state)
    existing["run_id"] = run_id

    dst = _path(run_id)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, default=str, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())

    logger.debug("[run_id=%s] state saved (phase=%s)", run_id, existing.get("phase"))
    return dst


def load_run_state(run_id: str) -> Optional[dict]:
    """Load run state from disk. Returns None if not found."""
    path = _path(run_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("[run_id=%s] failed to load state: %s", run_id, str(e))
        return None


def delete_run_state(run_id: str) -> bool:
    """Delete run state. Returns True if deleted."""
    path = _path(run_id)
    if os.path.exists(path):
        os.unlink(path)
        logger.debug("[run_id=%s] state deleted", run_id)
        return True
    return False


def transition_phase(run_id: str, target: RunPhase) -> dict:
    """Validate and persist a phase transition for a run.

    Returns the updated run state dict.

    Raises:
        ValueError: if transition is invalid.
    """
    current_state = load_run_state(run_id)
    if current_state is None:
        current_phase = RunPhase.INTERPRETING
    else:
        current_phase = RunPhase(current_state.get("phase", RunPhase.INTERPRETING.value))

    validate_transition(current_phase, target)

    updated = {"phase": target.value}
    save_run_state(run_id, updated)
    merged = load_run_state(run_id) or updated
    return merged
