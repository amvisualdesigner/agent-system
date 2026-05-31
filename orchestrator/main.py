import uuid
import asyncio
import json
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from state import AgentState
from graph import compiled_graph
from models import (
    RunRequest, RunResponse,
    ConfirmRequest, ApplyRequest,
    SSEEvent,
)
from run_id import validate_run_id
from sse import emitter
from store import save_snapshot, load_snapshot, list_snapshots

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("orchestrator.main")

background_tasks: dict[str, asyncio.Task] = {}


def _build_initial_state(run_id: str, task: str, start_node: str = "interpret") -> dict:
    return {
        "task": task,
        "run_id": run_id,
        "plan": None,
        "execution": None,
        "run_details": None,
        "error": None,
        "retry_count": 0,
        "trace": [],
        "phase": "planning",
        "cancelled": False,
        "backend_run_id": None,
        "planner_meta": None,
        "_next_node": None,
        "start_node": start_node,
        "interpretation": None,
        "confirmed_intent": None,
        "plan_preview": None,
    }


async def run_graph(state: dict):
    run_id = state["run_id"]
    logger.info("[run_id=%s] graph started start_node=%s task=%s", run_id, state.get("start_node"), (state.get("task") or "")[:80])
    try:
        await compiled_graph.ainvoke(state)
        logger.info("[run_id=%s] graph completed", run_id)
    except asyncio.CancelledError:
        logger.warning("[run_id=%s] graph cancelled", run_id)
        await emitter.emit(
            run_id,
            SSEEvent(type="error", phase="cancelled", run_id=run_id, error="cancelled"),
        )
    except Exception as e:
        logger.error("[run_id=%s] graph crashed: %s", run_id, str(e))
        await emitter.emit(
            run_id,
            SSEEvent(type="error", phase="error", run_id=run_id, error=str(e)),
        )
    finally:
        background_tasks.pop(run_id, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import store as _store
    _store._ensure_dir()
    existing = _store.list_snapshots()
    logger.info("startup: runs/ ready, %d past snapshots found", len(existing))
    yield
    for tid, task in background_tasks.items():
        task.cancel()
    background_tasks.clear()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/run", response_model=RunResponse)
async def create_run(req: RunRequest):
    run_id = str(uuid.uuid4())
    logger.info("[run_id=%s] POST /run task=%s", run_id, req.task[:80])

    emitter.register(run_id)

    state = _build_initial_state(run_id, req.task, start_node="interpret")
    task = asyncio.create_task(run_graph(state))
    background_tasks[run_id] = task

    return RunResponse(run_id=run_id)


@app.post("/run/{run_id}/confirm")
async def confirm_run(run_id: str, req: ConfirmRequest):
    validate_run_id(run_id)

    snapshot = load_snapshot(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="run not found")

    phase = snapshot.get("phase", "")
    if phase != "awaiting_confirmation":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot confirm in phase '{phase}'. Expected 'awaiting_confirmation'.",
        )

    if run_id in background_tasks:
        raise HTTPException(status_code=409, detail="run already in progress")

    logger.info("[run_id=%s] POST /run/%s/confirm", run_id, run_id)

    # Build confirmed_intent from user's confirmation + existing interpretation
    interpretation = snapshot.get("interpretation", {})
    confirmed_intent = {
        "contract_id": req.contract_id or interpretation.get("contract_id", ""),
        "contract_version": req.contract_version or interpretation.get("contract_version", 1),
        "actions": req.actions or interpretation.get("proposed_actions", []),
        "params": req.params or interpretation.get("params_proposed", {}),
        "user_message": req.user_message,
    }

    state = _build_initial_state(run_id, snapshot.get("task", ""), start_node="confirm")
    state["interpretation"] = interpretation
    state["confirmed_intent"] = confirmed_intent
    state["phase"] = "confirming"

    emitter.register(run_id)
    task = asyncio.create_task(run_graph(state))
    background_tasks[run_id] = task

    return {"status": "accepted", "run_id": run_id, "message": "confirming"}


@app.post("/run/{run_id}/apply")
async def apply_run(run_id: str, req: ApplyRequest = ApplyRequest()):
    validate_run_id(run_id)

    snapshot = load_snapshot(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="run not found")

    phase = snapshot.get("phase", "")
    if phase not in ("awaiting_apply",):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot apply in phase '{phase}'. Expected 'awaiting_apply'.",
        )

    if run_id in background_tasks:
        raise HTTPException(status_code=409, detail="run already in progress")

    # Check gate from plan_preview
    plan_preview = snapshot.get("plan_preview", {})
    gate = plan_preview.get("gate", {}) if isinstance(plan_preview, dict) else {}
    if gate.get("blocked", False):
        raise HTTPException(
            status_code=400,
            detail=f"Gate blocked: {gate.get('reason', 'unknown')}",
        )

    logger.info("[run_id=%s] POST /run/%s/apply dry_run=%s", run_id, run_id, req.dry_run)

    state = _build_initial_state(run_id, snapshot.get("task", ""), start_node="apply")
    state["interpretation"] = snapshot.get("interpretation")
    state["confirmed_intent"] = snapshot.get("confirmed_intent")
    state["plan"] = snapshot.get("plan")
    state["plan_preview"] = plan_preview
    state["phase"] = "applying"

    emitter.register(run_id)
    task = asyncio.create_task(run_graph(state))
    background_tasks[run_id] = task

    return {"status": "accepted", "run_id": run_id, "message": "applying"}


@app.get("/stream/{run_id}")
async def stream_run(run_id: str):
    validate_run_id(run_id)
    if not emitter.has_run(run_id):
        logger.warning("[run_id=%s] GET /stream not found", run_id)
        raise HTTPException(status_code=404, detail="run not found")

    logger.info("[run_id=%s] GET /stream connected", run_id)

    async def event_generator():
        try:
            async for event in emitter.subscribe(run_id):
                yield {"event": "message", "data": json.dumps(event.model_dump())}
        finally:
            logger.info("[run_id=%s] GET /stream disconnected", run_id)

    return EventSourceResponse(event_generator())


@app.get("/runs")
async def list_runs():
    snapshots = list_snapshots()
    return {"runs": snapshots, "count": len(snapshots)}


@app.get("/runs/{run_id}")
async def get_run_snapshot(run_id: str):
    validate_run_id(run_id)
    snapshot = load_snapshot(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="run snapshot not found")
    return snapshot


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
