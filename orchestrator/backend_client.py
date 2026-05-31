import asyncio
import logging
import os
from typing import Dict, Any
import httpx

logger = logging.getLogger("orchestrator.backend_client")

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

MAX_RETRIES = 2
RETRY_DELAY_MS = 500


async def call_interpret(task: str, run_id: str = "") -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            f"{BACKEND_URL}/agent/interpret",
            json={"run_id": run_id, "message": task, "conversation": []},
        )
        r.raise_for_status()
        return r.json()


async def call_confirm(payload: dict) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{BACKEND_URL}/agent/confirm", json=payload)
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
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(f"{BACKEND_URL}/runs/{run_id}")
                r.raise_for_status()
                data = r.json()
                logger.info(
                    "[run_id=%s] get_run attempt=%d ok exists=%s has_diff=%s has_files=%s",
                    run_id, attempt, data.get("exists"),
                    bool(data.get("diff")),
                    bool(data.get("files")),
                )
                return data
        except Exception as e:
            last_error = e
            logger.warning(
                "[run_id=%s] get_run attempt=%d/%d failed: %s",
                run_id, attempt, MAX_RETRIES, str(e),
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY_MS / 1000)

    logger.error(
        "[run_id=%s] get_run all %d attempts failed: %s",
        run_id, MAX_RETRIES, str(last_error),
    )
    raise last_error or RuntimeError("get_run failed after retries")
