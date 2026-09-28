import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.agent_apply import router as agent_apply
from app.api.agent_interpret import router as agent_interpret_router
from app.api.agent_confirm import router as agent_confirm_router
from app.utils.run_id import validate_run_id
from app.utils.path_guard import guard_within
from app.config.settings import settings

# -------- CONFIGURACIÓN --------
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
app = FastAPI()


@app.exception_handler(ValueError)
def value_error_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})
app.include_router(agent_apply)
app.include_router(agent_interpret_router)
app.include_router(agent_confirm_router)

# -------- MODELOS --------
class RepoFile(BaseModel):
    path: str
    content: str

# -------- ENDPOINTS --------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/runs/{run_id}")
def get_run(run_id: str):
    import os
    import json

    validate_run_id(run_id)

    workspace = f"{settings.RUNS_DIR}/{run_id}"
    guard_within(workspace, settings.RUNS_DIR)
    artifacts_dir = f"{settings.ARTIFACTS_DIR}/{run_id}"
    guard_within(artifacts_dir, settings.ARTIFACTS_DIR)

    result = {
        "run_id": run_id,
        "exists": os.path.exists(workspace) or os.path.exists(artifacts_dir),
    }

    # ----------------------------
    # 1. ARTIFACTS (source of truth)
    # ----------------------------
    if os.path.exists(artifacts_dir):

        def load_json(path):
            if os.path.exists(path):
                with open(path) as f:
                    return json.load(f)
            return None

        result["plan"] = load_json(f"{artifacts_dir}/plan.json")
        result["execution"] = load_json(f"{artifacts_dir}/execution.json")
        result["context"] = load_json(f"{artifacts_dir}/context.json")
        result["meta"] = load_json(f"{artifacts_dir}/meta.json")

    # ----------------------------
    # 2. WORKSPACE (ensure present in context)
    # ----------------------------
    if os.path.exists(workspace):
        ctx = result.get("context")
        if not ctx:
            result["context"] = {"workspace": workspace, "repo_snapshot": []}
        elif "workspace" not in ctx:
            ctx["workspace"] = workspace

    return result

