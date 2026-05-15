import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP
import httpx
import json

from app.utils.run_id import validate_run_id

mcp = FastMCP("agent-runtime")

BASE_URL = "http://localhost:8000"


# -------------------------
# HELPERS
# -------------------------
def normalize_plan(plan):
    if not isinstance(plan, dict):
        return None

    if "actions" in plan:
        return plan

    return None


# -------------------------
# PLAN
# -------------------------
@mcp.tool()
async def agent_plan(prompt: str):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/agent/plan",
            json={"task": prompt}
        )

    r.raise_for_status()
    return r.json()


# -------------------------
# APPLY (sandbox execution)
# -------------------------
@mcp.tool()
async def agent_apply(run_id: str, plan, dry_run: bool = False):
    validate_run_id(run_id)
    plan = normalize_plan(plan)

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{BASE_URL}/agent/apply",
            json={
                "run_id": run_id,
                "plan": plan,
                "dry_run": dry_run
            }
        )

    r.raise_for_status()
    return r.json()


# -------------------------
# RUN STATE / INSPECTION
# -------------------------
@mcp.tool()
async def get_run(run_id: str):
    validate_run_id(run_id)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{BASE_URL}/runs/{run_id}"
        )

    r.raise_for_status()
    return r.json()


# -------------------------
# REVIEW (PR VIEW TOOL)
# -------------------------
@mcp.tool()
async def agent_review(run_id: str):
    validate_run_id(run_id)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{BASE_URL}/runs/{run_id}"
        )

    r.raise_for_status()
    data = r.json()

    summary = data.get("summary", {})
    execution = data.get("execution", {})

    return {
        "run_id": run_id,
        "exists": data.get("exists"),
        "plan": data.get("plan"),
        "execution": execution,
        "summary": summary,
        "diff": data.get("diff"),
        "files_changed": data.get("files"),
        "workspace": data.get("workspace"),
        "operations": execution.get("operations"),
        "status": summary.get("status"),
    }


# -------------------------
# ONE-SHOT (fast mode)
# -------------------------
@mcp.tool()
async def agent_run(prompt: str, dry_run: bool = False):
    async with httpx.AsyncClient(timeout=120) as client:

        # 1. PLAN
        plan_resp = await client.post(
            f"{BASE_URL}/agent/plan",
            json={"task": prompt}
        )
        plan_resp.raise_for_status()
        plan_data = plan_resp.json()

        run_id = plan_data["run_id"]

        # 2. APPLY
        apply_resp = await client.post(
            f"{BASE_URL}/agent/apply",
            json={
                "run_id": run_id,
                "plan": plan_data["plan"],
                "dry_run": dry_run
            }
        )
        apply_resp.raise_for_status()

    return {
        "run_id": run_id,
        "plan": plan_data["plan"],
        "dry_run": dry_run,
        "result": apply_resp.json()
    }

# -------------------------
# APPROVE DIFF
# -------------------------
@mcp.tool()
async def agent_approve(run_id: str):
    validate_run_id(run_id)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{BASE_URL}/runs/{run_id}/approve"
        )

    r.raise_for_status()

    return {
        "run_id": run_id,
        "status": "approved",
        "result": r.json()
    }

# -------------------------
# REJECT DIFF
# -------------------------
@mcp.tool()
async def agent_reject(run_id: str):
    validate_run_id(run_id)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/runs/{run_id}/reject"
        )

    r.raise_for_status()

    return {
        "run_id": run_id,
        "status": "rejected",
        "result": r.json()
    }


# -------------------------
if __name__ == "__main__":
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = 8510
    mcp.run(transport="stdio", mount_path="/mcp")