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
# ONE-SHOT (auto: interpret → confirm → apply)
# -------------------------
@mcp.tool()
async def agent_run(prompt: str, dry_run: bool = False):
    """One-shot: interpret → auto-confirm with proposed actions → apply.

    Fast path — no human-in-the-loop. Uses the proposed_actions from the
    interpreter directly without allowing edits.
    """
    async with httpx.AsyncClient(timeout=180) as client:
        run_id = str(uuid.uuid4())

        # 1. INTERPRET
        interp_resp = await client.post(
            f"{BASE_URL}/agent/interpret",
            json={"run_id": run_id, "message": prompt, "conversation": []}
        )
        interp_resp.raise_for_status()
        interp_data = interp_resp.json()

        # 2. CONFIRM (auto-accept proposed actions)
        actions = interp_data.get("proposed_actions", [])
        confirm_resp = await client.post(
            f"{BASE_URL}/agent/confirm",
            json={
                "run_id": run_id,
                "interpretation_id": interp_data["interpretation_id"],
                "contract_id": interp_data["contract_id"],
                "actions": actions,
            }
        )
        confirm_resp.raise_for_status()
        confirm_data = confirm_resp.json()

        # 3. APPLY
        apply_resp = await client.post(
            f"{BASE_URL}/agent/apply",
            json={"run_id": run_id, "dry_run": dry_run}
        )
        apply_resp.raise_for_status()

    return {
        "run_id": run_id,
        "interpretation": interp_data,
        "plan_preview": confirm_data.get("plan_preview"),
        "dry_run": dry_run,
        "result": apply_resp.json()
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
# APPROVE DIFF
# -------------------------
@mcp.tool()
async def agent_approve(run_id: str):
    validate_run_id(run_id)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{BASE_URL}/runs/{run_id}/approve")
    r.raise_for_status()
    return {"run_id": run_id, "status": "approved", "result": r.json()}


# -------------------------
# REJECT DIFF
# -------------------------
@mcp.tool()
async def agent_reject(run_id: str):
    validate_run_id(run_id)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{BASE_URL}/runs/{run_id}/reject")
    r.raise_for_status()
    return {"run_id": run_id, "status": "rejected", "result": r.json()}


# -------------------------
if __name__ == "__main__":
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = 8510
    mcp.run(transport="stdio", mount_path="/mcp")
