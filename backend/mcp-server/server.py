import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP
import httpx
import json

from app.utils.run_id import validate_run_id
from app.utils.session_id import validate_session_id

mcp = FastMCP("agent-runtime")

BASE_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


# -------------------------
# HELPERS
# -------------------------

# -------------------------
# INTERPRET
# -------------------------
@mcp.tool()
async def agent_interpret(prompt: str, run_id: str = None, session_id: str = None):
    """Interpret a user prompt into a structured intent (step 1 of 3).

    Returns an InterpretationDraft with proposed_actions, contract_id, and interpretation_id.
    Pass those to agent_confirm for step 2.
    session_id (optional): run this Run inside an existing Session.
    """
    if run_id is None:
        run_id = str(uuid.uuid4())

    payload = {"run_id": run_id, "message": prompt, "conversation": []}
    if session_id:
        validate_session_id(session_id)
        payload["session_id"] = session_id

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{BASE_URL}/agent/interpret", json=payload)

    r.raise_for_status()
    data = r.json()
    data["run_id"] = run_id
    return data


# -------------------------
# CONFIRM
# -------------------------
@mcp.tool()
async def agent_confirm(run_id: str, interpretation_id: str, contract_id: str,
                        actions: list, params: dict = None,
                        contract_version: int = 1):
    """Confirm an interpreted intent and compile the execution plan (step 2 of 3).

    Args:
        run_id: Run ID from agent_interpret response.
        interpretation_id: interpretation_id from agent_interpret response.
        contract_id: contract_id from agent_interpret response.
        actions: List of proposed_actions from agent_interpret response. Each item
                 should have 'verb' and 'target_capability'.
        params: Optional parameters (e.g. {"metrics": ["sales", "units"]}).
        contract_version: Contract version (default 1).
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/agent/confirm",
            json={
                "run_id": run_id,
                "interpretation_id": interpretation_id,
                "contract_id": contract_id,
                "contract_version": contract_version,
                "actions": actions,
                "params": params or {},
            }
        )

    r.raise_for_status()
    return r.json()


# -------------------------
# APPLY
# -------------------------
@mcp.tool()
async def agent_apply(run_id: str, dry_run: bool = False):
    """Apply a confirmed plan (step 3 of 3).

    Plan must have been confirmed via agent_confirm first.
    The compiled plan is loaded from run state — no plan dict needed.
    """
    validate_run_id(run_id)

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{BASE_URL}/agent/apply",
            json={"run_id": run_id, "dry_run": dry_run}
        )

    r.raise_for_status()
    return r.json()


# -------------------------
# RETRY / CANCEL (Fase 5B)
# -------------------------
@mcp.tool()
async def agent_retry(run_id: str, dry_run: bool = False):
    """Re-apply the SAME Confirmed Plan after a conflict.

    Re-captures the physical baseline (fresh snapshot) and re-runs the apply.
    It never reinterprets, recompiles or modifies the Plan (WHAT/WHERE frozen).
    Only valid for a Run parked in 'confirmed' after a conflict.
    """
    validate_run_id(run_id)

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{BASE_URL}/agent/retry",
            json={"run_id": run_id, "dry_run": dry_run}
        )

    if r.status_code >= 400:
        return {"run_id": run_id, "status": "rejected", "detail": r.text}
    return r.json()


@mcp.tool()
async def agent_cancel(run_id: str, reason: str = None):
    """Cancel a Run before apply starts.

    Allowed only in interpreting / awaiting_confirmation / confirmed.
    Not allowed while applying. No artifact, Plan or worktree change is produced.
    """
    validate_run_id(run_id)

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/agent/cancel",
            json={"run_id": run_id, "reason": reason}
        )

    if r.status_code >= 400:
        return {"run_id": run_id, "status": "rejected", "detail": r.text}
    return r.json()


# -------------------------
# ONE-SHOT (interpret only, human-in-the-loop)
# -------------------------
@mcp.tool()
async def agent_run(prompt: str, session_id: str = None):
    """Interpret a prompt and leave the Run in awaiting_confirmation.

    S1-A.5: agent_run never confirms nor applies. It delegates to the same
    canonical /agent/interpret→/agent/confirm→/agent/apply flow as the API and
    returns the Run waiting for an explicit human confirmation. It cannot
    produce confirmed/applying/completed on its own.
    session_id (optional): run this Run inside an existing Session.
    """
    async with httpx.AsyncClient(timeout=180) as client:
        run_id = str(uuid.uuid4())

        payload = {"run_id": run_id, "message": prompt, "conversation": []}
        if session_id:
            validate_session_id(session_id)
            payload["session_id"] = session_id

        interp_resp = await client.post(f"{BASE_URL}/agent/interpret", json=payload)
        interp_resp.raise_for_status()
        interp_data = interp_resp.json()

    return {
        "run_id": run_id,
        "interpretation": interp_data,
        "status": "awaiting_confirmation",
        "detail": (
            "Run created in awaiting_confirmation. Confirm via agent_confirm, "
            "then execute via agent_apply. agent_run performs no confirmation "
            "and no application on its own."
        ),
    }


# -------------------------
# RUN STATE / INSPECTION
# -------------------------
@mcp.tool()
async def get_run(run_id: str):
    validate_run_id(run_id)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{BASE_URL}/runs/{run_id}")
    r.raise_for_status()
    return r.json()


# -------------------------
# REVIEW (PR VIEW TOOL)
# -------------------------
@mcp.tool()
async def agent_review(run_id: str):
    validate_run_id(run_id)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{BASE_URL}/runs/{run_id}")
    r.raise_for_status()
    data = r.json()

    execution = data.get("execution", {})
    context = data.get("context", {})

    return {
        "run_id": run_id,
        "exists": data.get("exists"),
        "plan": data.get("plan"),
        "execution": execution,
        "diff": execution.get("diff"),
        "files_changed": context.get("repo_snapshot"),
        "workspace": context.get("workspace"),
        "operations": execution.get("operations"),
        "status": execution.get("status"),
        "run_phase": data.get("run_phase"),
        "stage": data.get("stage"),
        "plan_confirmed": data.get("plan_confirmed"),
        "plan_retryable": data.get("plan_retryable"),
        "last_conflict": data.get("last_conflict"),
    }


# -------------------------
# SESSION — physical continuity + lifecycle (S1-B)
# -------------------------
@mcp.tool()
async def agent_create_session(base_branch: str = "master"):
    """Create a new Session (ACTIVE): one worktree + one branch from base.

    Returns session_id, branch, workspace, status. Runs can then be created
    inside it by passing session_id to agent_run / agent_interpret.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{BASE_URL}/session", json={"base_branch": base_branch})
    r.raise_for_status()
    return r.json()


@mcp.tool()
async def agent_session_status(session_id: str):
    """Inspect a Session: status, derived merge_ready, reasons, and its Runs.

    merge_ready is derived at read-time from merge preconditions (never
    persisted as a state).
    """
    validate_session_id(session_id)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{BASE_URL}/session/{session_id}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
async def agent_session_merge(session_id: str):
    """Integrate a Session into the base branch (the ONLY integration route).

    Performs git merge --no-ff with message session:{session_id[:8]}. On unmet
    preconditions or a git conflict, the base is restored (git merge --abort)
    and the Session becomes CONFLICT (recoverable). No auto-resolution.
    """
    validate_session_id(session_id)
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{BASE_URL}/session/{session_id}/merge")
    r.raise_for_status()
    return r.json()


# -------------------------
if __name__ == "__main__":
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = 8510
    mcp.run(transport="stdio", mount_path="/mcp")
