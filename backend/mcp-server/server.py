import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP
import httpx
import json

from app.utils.run_id import validate_run_id

mcp = FastMCP("agent-runtime")

BASE_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


# -------------------------
# HELPERS
# -------------------------

# -------------------------
# INTERPRET
# -------------------------
@mcp.tool()
async def agent_interpret(prompt: str, run_id: str = None):
    """Interpret a user prompt into a structured intent (step 1 of 3).

    Returns an InterpretationDraft with proposed_actions, contract_id, and interpretation_id.
    Pass those to agent_confirm for step 2.
    """
    if run_id is None:
        run_id = str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{BASE_URL}/agent/interpret",
            json={"run_id": run_id, "message": prompt, "conversation": []}
        )

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
# ONE-SHOT (interpret only, human-in-the-loop)
# -------------------------
@mcp.tool()
async def agent_run(prompt: str):
    """Interpret a prompt and leave the Run in awaiting_confirmation.

    S1-A.5: agent_run never confirms nor applies. It delegates to the same
    canonical /agent/interpret→/agent/confirm→/agent/apply flow as the API and
    returns the Run waiting for an explicit human confirmation. It cannot
    produce confirmed/applying/completed on its own.
    """
    async with httpx.AsyncClient(timeout=180) as client:
        run_id = str(uuid.uuid4())

        interp_resp = await client.post(
            f"{BASE_URL}/agent/interpret",
            json={"run_id": run_id, "message": prompt, "conversation": []}
        )
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
    }


# -------------------------
# APPROVE — pending migration to session_merge
# -------------------------
@mcp.tool()
async def agent_approve(run_id: str):
    """Approve Run changes for integration.

    S1-A.4: the legacy approve→commit→merge path has been removed. A Run's
    commit belongs to its successful Apply (confirm→apply→commit), and there is
    no per-run merge into the base branch. Integration of session branches is
    the future agent_session_merge(session_id) operation — not implemented in
    the current model. This tool is explicitly marked pending that migration:
    it performs no commit, no merge, and no branch deletion.
    """
    validate_run_id(run_id)
    return {
        "run_id": run_id,
        "status": "pending_session_merge_migration",
        "detail": (
            "approve Run → commit → merge no longer exists. Commits belong to "
            "the successful Apply; base-branch integration will be handled by "
            "agent_session_merge(session_id) in the Session phase. Inspect the "
            "Run with agent_review and verify its Apply result instead."
        ),
    }


# -------------------------
if __name__ == "__main__":
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = 8510
    mcp.run(transport="stdio", mount_path="/mcp")
