from mcp.server.fastmcp import FastMCP
import httpx
import json

mcp = FastMCP("agent-runtime")

BASE_URL = "http://localhost:8000"


# -------------------------
# HELPERS
# -------------------------
def normalize_plan(plan):
    if isinstance(plan, str):
        return json.loads(plan)
    if isinstance(plan, dict):
        return plan
    raise ValueError("Invalid plan format")


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
async def agent_apply(run_id: str, plan):
    plan = normalize_plan(plan)

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{BASE_URL}/agent/apply",
            json={
                "run_id": run_id,
                "plan": plan
            }
        )

    r.raise_for_status()
    return r.json()


# -------------------------
# RUN STATE / INSPECTION
# -------------------------
@mcp.tool()
async def get_run(run_id: str):
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
async def agent_run(prompt: str):
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
                "plan": plan_data["plan"]
            }
        )
        apply_resp.raise_for_status()

    return {
        "run_id": run_id,
        "plan": plan_data["plan"],
        "result": apply_resp.json()
    }

# -------------------------
# APPROVE DIFF
# -------------------------
@mcp.tool()
async def agent_approve(run_id: str):
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
    mcp.run()