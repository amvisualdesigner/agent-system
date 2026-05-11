from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any
import os
import json
import uuid
import subprocess
import shutil
import time

from app.executor.patch_executor import apply_operation
from app.utils.workspace import list_workspace_files
from app.planner.plan_generator import build_prompt, call_llm
from app.planner.plan_validator import validate_plan
from app.planner.execution_compiler import compile_plan

# -------- CONFIGURACIÓN --------
MAX_OPERATIONS = 20
MAX_DELETES = 3

app = FastAPI()

# -------- MODELOS --------
class RepoFile(BaseModel):
    path: str
    content: str

class AgentRequest(BaseModel):
    task: str
    scope: List[str] = []
    constraints: List[str] = []
    repo_context: List[RepoFile] = []

# -------- ENDPOINTS --------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/agent/run")
def run_agent(req: AgentRequest):
    REPO_ROOT = "/opt/agent-repos/agent-test-repo"

    run_id = str(uuid.uuid4())
    branch = f"agent-run-{run_id}"
    base_dir = f"/tmp/agent-runs/{run_id}"
    workspace = f"{base_dir}/workspace"
    artifacts_dir = f"{base_dir}/artifacts"

    # estado del run (importante para cleanup seguro)
    worktree_created = False

    # limpieza inicial
    shutil.rmtree(base_dir, ignore_errors=True)
    os.makedirs(workspace, exist_ok=True)
    os.makedirs(artifacts_dir, exist_ok=True)

    try:
        # limpiar worktrees huérfanos
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True
        )

        # ----------------------------
        # 1. crear worktree
        # ----------------------------
        branch = f"agent-run-{run_id}"

        result = subprocess.run(
            ["git", "worktree", "add", workspace, "-b", branch],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise Exception(result.stderr)

        worktree_created = True

        # ----------------------------
        # 2. contexto + prompt
        # ----------------------------
        workspace_files = list_workspace_files(workspace)
        prompt = build_prompt(req.task, workspace_files)

        with open(f"{artifacts_dir}/request.json", "w") as f:
            json.dump(req.dict(), f, indent=2)

        # ----------------------------
        # 3. LLM
        # ----------------------------
        t0 = time.time()

        llm_result = call_llm(prompt)
        
        t1 = time.time()
        print("LLM:", t1 - t0)

        # Plan
        plan = llm_result
        ok, reason = validate_plan(plan)
        if not ok:
            return {
                "error": "invalid_plan",
                "reason": reason,
                "plan": plan
        }

        print("=== PLAN ===")
        print(json.dumps(plan, indent=2))

        # Artifacts
        with open(f"{artifacts_dir}/llm_raw.json", "w") as f:
            json.dump(llm_result, f, indent=2)

        # Execution
        if "operations" not in llm_result:
            llm_result = {
                "operations": [],
                "warnings": ["missing_operations"],
                "raw": llm_result
            }
        operations = compile_plan(plan)

        print("=== COMPILED OPERATIONS ===")
        print(json.dumps(operations, indent=2))

        with open(f"{artifacts_dir}/operations.json", "w") as f:
            json.dump(operations, f, indent=2)

        # ----------------------------
        # 4. seguridad (policy layer)
        # ----------------------------
        delete_ops = [op for op in operations if op.get("type") == "delete"]

        if len(delete_ops) > MAX_DELETES:
            return {
                "error": "too_many_deletes",
                "limit": MAX_DELETES,
                "received": len(delete_ops)
            }

        if len(operations) > MAX_OPERATIONS:
            return {"error": "too_many_operations"}

        # ----------------------------
        # 5. ejecución
        # ----------------------------
        execution = []

        t2 = time.time()

        for op in operations:
            execution.append(apply_operation(op, workspace))

        t3 = time.time()
        print("EXEC:", t3 - t2)

        # ----------------------------
        # 6. git diff
        # ----------------------------
        subprocess.run(
            ["git", "add", "-A"],
            cwd=workspace,
            capture_output=True,
            text=True
        )

        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=workspace,
            capture_output=True,
            text=True
        )

        t4 = time.time()

        git_diff = diff_result.stdout

        t5 = time.time()
        print("GIT:", t5 - t4)

        with open(f"{artifacts_dir}/diff.patch", "w") as f:
            f.write(git_diff)

        with open("/tmp/agent-runs/LAST_RUN.txt", "w") as f:
            f.write(run_id)
            

        # ----------------------------
        # 7. respuesta
        # ----------------------------
        return {
            "run_id": run_id,
            "workspace": workspace,
            "llm_result": llm_result,
            "execution": execution,
            "git_diff": git_diff,
            "artifacts": artifacts_dir,
        }

    finally:
        # ----------------------------
        # CLEANUP SEGURO
        # ----------------------------
        try:
            if worktree_created:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", workspace],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True
                )
                
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True
                )
        except Exception:
            pass

        shutil.rmtree(base_dir, ignore_errors=True)

@app.get("/agent/latest")
def latest():
    path = "/tmp/agent-runs/LAST_RUN.txt"

    if not os.path.exists(path):
        return {"error": "no runs yet"}

    with open(path) as f:
        run_id = f.read().strip()

    return {"run_id": run_id}