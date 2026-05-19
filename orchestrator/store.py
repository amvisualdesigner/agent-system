import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from run_id import validate_run_id, guard_within

logger = logging.getLogger("orchestrator.store")

RUNS_DIR = os.path.join(os.path.dirname(__file__), "runs")


def _ensure_dir():
    os.makedirs(RUNS_DIR, exist_ok=True)


def _path(run_id: str) -> str:
    validate_run_id(run_id)
    path = os.path.join(RUNS_DIR, f"{run_id}.json")
    guard_within(path, RUNS_DIR)
    return path


def save_snapshot(run_id: str, snapshot: dict) -> str:
    _ensure_dir()
    validate_run_id(run_id)

    snapshot["run_id"] = run_id
    snapshot["updated_at"] = datetime.now(timezone.utc).isoformat()
    if "created_at" not in snapshot:
        snapshot["created_at"] = snapshot["updated_at"]

    dst = _path(run_id)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, default=str, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())

    logger.info("[run_id=%s] snapshot saved to %s (%d bytes)", run_id, dst, os.path.getsize(dst))
    return dst


def load_snapshot(run_id: str) -> Optional[dict]:
    validate_run_id(run_id)
    path = _path(run_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("[run_id=%s] failed to load snapshot: %s", run_id, str(e))
        return None


def list_snapshots() -> List[Dict[str, Any]]:
    _ensure_dir()
    snapshots = []
    for name in os.listdir(RUNS_DIR):
        if not name.endswith(".json"):
            continue
        run_id = name[:-5]
        try:
            path = os.path.join(RUNS_DIR, name)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            snapshots.append({
                "run_id": run_id,
                "status": data.get("status"),
                "phase": data.get("phase"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "task": (data.get("task") or "")[:80],
                "files": data.get("files"),
                "error": data.get("error"),
            })
        except Exception as e:
            logger.warning("failed to index snapshot %s: %s", name, str(e))
    snapshots.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return snapshots
