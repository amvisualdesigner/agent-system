import logging
import subprocess
import os
import shutil

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.agent_apply import router as agent_apply
from app.api.agent_interpret import router as agent_interpret_router
from app.api.agent_confirm import router as agent_confirm_router
from app.utils.state import read_state
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

@app.get("/agent/latest")
def latest():
    state = read_state()

    if not state:
        return {"error": "no runs yet"}

    return state

@app.get("/maintenance/cleanup")  # local-only debug endpoint
def cleanup():
    print("[cleanup] START")

    repo_root = settings.REPO_ROOT

    # ----------------------------
    # 1. SAFE WORKTREE REMOVAL
    # ----------------------------
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False
    )

    for line in result.stdout.splitlines():
        if not line.startswith("worktree "):
            continue

        wt = line[len("worktree "):].strip()

        # HARD SAFETY: only allow inside RUNS_DIR
        if not wt.startswith(os.path.abspath(settings.RUNS_DIR)):
            continue

        print(f"[cleanup] removing worktree {wt}")

        subprocess.run(
            ["git", "worktree", "remove", wt, "--force"],
            cwd=repo_root,
            check=False
        )

    # ----------------------------
    # 2. DELETE MERGED BRANCHES
    # ----------------------------
    result = subprocess.run(
        ["git", "branch", "--merged"],
        cwd=repo_root,
        capture_output=True,
        text=True
    )

    for b in result.stdout.splitlines():
        b = b.strip().replace("*", "").strip()

        if b.startswith("agent-"):
            print(f"[cleanup] deleting merged branch {b}")
            subprocess.run(
                ["git", "branch", "-d", b],
                cwd=repo_root,
                check=False
            )

    # ----------------------------
    # 3. CLEAN RUNS_DIR SAFELY
    # ----------------------------
    runs_dir = os.path.abspath(settings.RUNS_DIR)

    if os.path.exists(runs_dir):
        for name in os.listdir(runs_dir):
            path = os.path.abspath(os.path.join(runs_dir, name))

            # SAFETY: enforce containment
            if not path.startswith(runs_dir):
                continue

            if name == "state.json":
                continue

            print(f"[cleanup] removing {path}")
            shutil.rmtree(path, ignore_errors=True)

    # ----------------------------
    # 4. CLEAN ARTIFACTS DIR SAFELY
    # ----------------------------
    artifacts_dir = os.path.abspath(settings.ARTIFACTS_DIR)

    if os.path.exists(artifacts_dir):
        for name in os.listdir(artifacts_dir):
            path = os.path.abspath(os.path.join(artifacts_dir, name))

            if not path.startswith(artifacts_dir):
                continue

            print(f"[cleanup] removing artifact {path}")
            shutil.rmtree(path, ignore_errors=True)

    # ----------------------------
    # 5. FINAL PRUNE
    # ----------------------------
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=repo_root,
        check=False
    )

    print("[cleanup] DONE")

    return {
        "status": "ok",
        "message": "cleanup completed"
    }

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

    validate_run_id(run_id)

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
    # 1.5. COMMIT STAGED CHANGES (dry_run recovery)
    # ----------------------------
    workspace = f"{settings.RUNS_DIR}/{run_id}"
    guard_within(workspace, settings.RUNS_DIR)
    if os.path.exists(workspace):
        subprocess.run(
            ["git", "add", "-A"],
            cwd=workspace,
            check=False
        )
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=workspace,
            check=False
        )
        if staged.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", f"agent:{run_id}"],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True
            )
            print(f"[approve] committed staged changes from dry_run")

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
    merge = subprocess.run(
        ["git", "merge", branch, "--no-edit"],
        cwd=settings.REPO_ROOT,
        capture_output=True,
        text=True
    )

    if merge.returncode != 0:

        print("[approve][ERROR] merge failed")
        print(merge.stderr)

        return {
            "status": "rejected",
            "reason": "merge_conflict",
            "error": merge.stderr
        }

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

