import os
from typing import Dict, Any, Optional
import httpx

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


async def call_plan(task: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(f"{BACKEND_URL}/agent/plan", json={"task": task})
        r.raise_for_status()
        return r.json()


async def call_apply(run_id: str, plan: dict, dry_run: bool = False) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{BACKEND_URL}/agent/apply",
            json={"run_id": run_id, "plan": plan, "dry_run": dry_run},
        )
        r.raise_for_status()
        return r.json()


async def get_run(run_id: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{BACKEND_URL}/runs/{run_id}")
        r.raise_for_status()
        return r.json()
