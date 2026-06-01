import os
import json
import time
from app.config.settings import settings

STATE_PATH =f"{settings.RUNS_DIR}/state.json"

def write_state(run_id: str, status: str):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)

        state = {
            "last_run_id": run_id,
            "status": status,
            "timestamp": time.time()
        }

        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)
    except PermissionError:
        pass  # Non-critical — global bookkeeping only


def read_state():
    if not os.path.exists(STATE_PATH):
        return None

    with open(STATE_PATH) as f:
        return json.load(f)