# main.py
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any
import os
import json
import uuid
import subprocess
import shutil

from app.executor.patch_executor import apply_operation
from app.utils.workspace import list_workspace_files
from app.executor.policy import safe_path, validate_operation

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

# -------- UTILIDADES LLM --------
def clean_json(text: str) -> str:
    """Extrae JSON robustamente del output del modelo."""
    text = text.strip()
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        text = text[first:last + 1]
    return text

def call_llm(prompt: str) -> Dict[str, Any]:
    import ollama
    response = ollama.chat(
        model="qwen3:14b",
        messages=[
            {"role": "system", "content": (
                "You are a strict code generation engine. "
                "You MUST return ONLY valid JSON. "
                "No markdown. No explanations. No text outside JSON."
            )},
            {"role": "user", "content": prompt}
        ]
    )
    raw = clean_json(response["message"]["content"])
    try:
        return json.loads(raw)
    except Exception as e:
        return {"error": "invalid_json_from_llm", "exception": str(e), "raw": raw}

# -------- PROMPT ENGINE --------
def build_prompt(task: str, workspace_files: list = []) -> str:
    context = "\n".join(workspace_files) if workspace_files else ""
    return f"""
Return ONLY valid JSON.

You are operating inside a git worktree.

Workspace files (existing structure):
{context}

You MUST only modify, create or delete files inside this workspace.
You MUST NOT use paths outside this list unless creating new files logically inside the project structure.

Schema:
{{
  "operations": [
    {{
      "type": "create",
      "path": "src/file.ts",
      "diff": "..."
    }}
  ],
  "warnings": []
}}

Rules:
- NO markdown
- NO explanations
- ALWAYS include "operations"

Task:
{task}
"""

# -------- ENDPOINTS --------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/agent/run")
def run_agent(req: AgentRequest):
    REPO_ROOT = "/opt/agent-repos/agent-test-repo"
    run_id = str(uuid.uuid4())
    base_dir = f"/tmp/agent-runs/{run_id}"
    workspace = f"{base_dir}/workspace"
    artifacts_dir = f"{base_dir}/artifacts"

    # limpieza y creación de directorios
    shutil.rmtree(base_dir, ignore_errors=True)
    os.makedirs(workspace, exist_ok=True)
    os.makedirs(artifacts_dir, exist_ok=True)

    # limpiar worktrees zombies
    subprocess.run(["git", "worktree", "prune"], cwd=REPO_ROOT, capture_output=True, text=True)

    branch = f"agent-run-{run_id}"
    subprocess.run(["git", "worktree", "add", workspace, "-b", branch],
                   cwd=REPO_ROOT, check=True)

    # contexto + prompt
    workspace_files = list_workspace_files(workspace)
    prompt = build_prompt(req.task, workspace_files)

    # guardar request
    with open(f"{artifacts_dir}/request.json", "w") as f:
        json.dump(req.dict(), f, indent=2)

    # llamar LLM
    llm_result = call_llm(prompt)

    with open(f"{artifacts_dir}/llm_raw.json", "w") as f:
        json.dump(llm_result, f, indent=2)

    # asegurar que siempre hay operaciones
    if "operations" not in llm_result:
        llm_result = {"operations": [], "warnings": ["missing_operations"], "raw": llm_result}

    operations = llm_result.get("operations", [])
    with open(f"{artifacts_dir}/operations.json", "w") as f:
        json.dump(operations, f, indent=2)

    # ------- SEGURIDAD -------
    delete_ops = [op for op in operations if op.get("type") == "delete"]
    if len(delete_ops) > MAX_DELETES:
        return {"error": "too_many_deletes", "limit": MAX_DELETES, "received": len(delete_ops)}
    if len(operations) > MAX_OPERATIONS:
        return {"error": "too_many_operations"}

    # ------- EJECUCIÓN SEGURA -------
    execution = [apply_operation(op, workspace) for op in operations]

    # ------- GIT DIFF -------
    subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True, text=True)
    diff_result = subprocess.run(["git", "diff", "--cached"], cwd=workspace, capture_output=True, text=True)
    git_diff = diff_result.stdout
    with open(f"{artifacts_dir}/diff.patch", "w") as f:
        f.write(git_diff)

    return {
        "run_id": run_id,
        "workspace": workspace,
        "llm_result": llm_result,
        "execution": execution,
        "git_diff": git_diff,
        "artifacts": artifacts_dir,
    }