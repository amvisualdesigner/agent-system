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
from models import RunRequest, RunResponse, SSEEvent
from run_id import validate_run_id
from sse import emitter
from store import list_snapshots, load_snapshot

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("orchestrator.main")

background_tasks: dict[str, asyncio.Task] = {}


async def run_graph(run_id: str, task: str):
    initial_state = {
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
        "_next_node": None,
    }

    logger.info("[run_id=%s] graph started task=%s", run_id, task[:80])
    try:
        await compiled_graph.ainvoke(initial_state)
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

    task = asyncio.create_task(run_graph(run_id, req.task))
    background_tasks[run_id] = task

    return RunResponse(run_id=run_id)


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
