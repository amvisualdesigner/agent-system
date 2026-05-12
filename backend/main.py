import subprocess
import os
import shutil

from fastapi import FastAPI, HTTPException
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


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    import json

    base = f"/tmp/agent-runs/{run_id}"
    artifacts_dir = f"{base}/artifacts"
    result = {"run_id": run_id, "exists": os.path.exists(base)}

    if os.path.exists(artifacts_dir):
        plan_path = f"{artifacts_dir}/plan.json"
        exec_path = f"{artifacts_dir}/execution.json"
        summary_path = f"{artifacts_dir}/summary.json"
        diff_path = f"{artifacts_dir}/diff.patch"

        if os.path.exists(plan_path):
            with open(plan_path) as f:
                result["plan"] = json.load(f)
        if os.path.exists(exec_path):
            with open(exec_path) as f:
                result["execution"] = json.load(f)
        if os.path.exists(summary_path):
            with open(summary_path) as f:
                result["summary"] = json.load(f)
        if os.path.exists(diff_path):
            with open(diff_path) as f:
                result["diff"] = f.read()

        workspace = f"{base}/workspace"
        if os.path.exists(workspace):
            ws_files = []

            for root, dirs, files in os.walk(workspace):
                for fn in files:
                    ws_files.append(os.path.join(root, fn).replace(workspace, "").lstrip("/"))

            result["workspace"] = workspace
            result["files"] = ws_files

    return result


def get_base_branch():
    result = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=settings.REPO_ROOT,
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        return result.stdout.strip().split("/")[-1]

    return "master"

# -------- APPROVE / REJECT --------
@app.post("/runs/{run_id}/approve")
def approve_run(run_id: str):

    branch = f"agent-{run_id[:8]}"
    base_branch = get_base_branch()

    print(f"[approve] run_id={run_id} branch={branch}")

    # ----------------------------
    # 1. CHECK BRANCH EXISTS
    # ----------------------------
    result = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=settings.REPO_ROOT,
        capture_output=True,
        text=True
    )

    if branch not in result.stdout:
        raise HTTPException(
            status_code=404,
            detail=f"branch {branch} not found"
        )

    # ----------------------------
    # 2. UPDATE BASE
    # ----------------------------

    subprocess.run(
        ["git", "checkout", base_branch],
        cwd=settings.REPO_ROOT,
        check=True
    )

    subprocess.run(
        ["git", "pull", "--rebase"],
        cwd=settings.REPO_ROOT,
        check=False
    )

    # ----------------------------
    # 3. MERGE BRANCH
    # ----------------------------
    subprocess.run(
        ["git", "merge", branch, "--no-edit"],
        cwd=settings.REPO_ROOT,
        check=True
    )

    print(f"[approve] merged {branch} into {base_branch}")

    # ----------------------------
    # 4. OPTIONAL CLEANUP (NO WORKTREES HERE)
    # ----------------------------
    subprocess.run(
        ["git", "branch", "-D", branch],
        cwd=settings.REPO_ROOT,
        check=False
    )

    return {
        "status": "ok",
        "run_id": run_id,
        "branch": branch,
        "merged_into": base_branch
    }

@app.post("/runs/{run_id}/reject")
def reject_run(run_id: str):

    # TODO

    return {
        "status": "ok",
        "run_id": run_id,
        "action": "rejected"
    }