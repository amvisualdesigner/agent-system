from mcp.server.fastmcp import FastMCP
import httpx
import json

mcp = FastMCP("agent-runtime")

BASE_URL = "http://localhost:8000"


# -------------------------
# PLAN
# -------------------------
@mcp.tool()
async def agent_plan(prompt: str):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/agent/plan",
            json={"input": prompt}
        )

    r.raise_for_status()
    return r.json()


# -------------------------
# APPLY
# -------------------------
@mcp.tool()
async def agent_apply(run_id: str, plan):
    # Normalización defensiva (clave para MCP + LLM tools)
    if isinstance(plan, str):
        plan = json.loads(plan)

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
# GET RUN
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
# OPTIONAL: ONE-SHOT TOOL (MUY ÚTIL PARA OPENCODE)
# -------------------------
@mcp.tool()
async def agent_run(prompt: str):
    async with httpx.AsyncClient(timeout=120) as client:

        plan_resp = await client.post(
            f"{BASE_URL}/agent/plan",
            json={"input": prompt}
        )
        plan_resp.raise_for_status()
        plan_data = plan_resp.json()

        run_id = plan_data["run_id"]

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
if __name__ == "__main__":
    mcp.run()