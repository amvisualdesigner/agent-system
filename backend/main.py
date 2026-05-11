import subprocess
import os
import shutil

from fastapi import FastAPI
from pydantic import BaseModel

from app.api.agent_plan import router as agent_plan_router
from app.api.agent_apply import router as agent_apply
from app.utils.state import read_state
from app.config.settings import settings

# -------- CONFIGURACIÓN --------
app = FastAPI()
app.include_router(agent_plan_router)
app.include_router(agent_apply)

# -------- MODELOS --------
class RepoFile(BaseModel):
    path: str
    content: str

# -------- ENDPOINTS --------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/agent/latest")
def latest():
    state = read_state()

    if not state:
        return {"error": "no runs yet"}

    return state

@app.get("/maintenance/cleanup") # TODO: remove this in prod
def cleanup():
    print("[cleanup] START")

    # ----------------------------
    # 1. GIT WORKTREES
    # ----------------------------
    subprocess.run(
        ["git", "worktree", "prune", "--force"],
        cwd=settings.REPO_ROOT,
        check=False
    )

    # ----------------------------
    # 2. DELETE agent BRANCHES
    # ----------------------------
    result = subprocess.run(
        ["git", "branch"],
        cwd=settings.REPO_ROOT,
        capture_output=True,
        text=True
    )

    branches = result.stdout.splitlines()

    for b in branches:
        b = b.strip().replace("*", "").strip()

        if b.startswith("agent-"):
            print(f"[cleanup] deleting branch {b}")
            subprocess.run(
                ["git", "branch", "-D", b],
                cwd=settings.REPO_ROOT,
                check=False
            )

    # ----------------------------
    # 3. WIPE /tmp/agent-runs
    # ----------------------------
    if os.path.exists(settings.RUNS_DIR):
        for name in os.listdir(settings.RUNS_DIR):
            path = os.path.join(settings.RUNS_DIR, name)

            print(f"[cleanup] removing {path}")

            try:
                shutil.rmtree(path)
            except Exception as e:
                print(f"[cleanup][WARN] failed to remove {path}: {e}")

    # ----------------------------
    # 4. FINAL
    # ----------------------------
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=settings.REPO_ROOT,
        check=False
    )

    print("[cleanup] DONE")

    return {
        "status": "ok",
        "message": "cleanup completed"
    }