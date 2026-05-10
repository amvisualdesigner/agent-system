from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any
import json
import ollama
import uuid
import subprocess
from app.utils.workspace import list_workspace_files

MAX_OPERATIONS = 20
MAX_DELETES = 3

from app.executor.patch_executor import (
    apply_operation,
    get_git_diff,
)

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

# -------- LLM UTILS --------

def clean_json(text: str):
    """
    Extracción robusta de JSON desde output del modelo.
    """
    text = text.strip()

    first = text.find("{")
    last = text.rfind("}")

    if first != -1 and last != -1 and last > first:
        text = text[first:last + 1]

    return text


def call_llm(prompt: str) -> Dict[str, Any]:
    response = ollama.chat(
        model="qwen3:14b",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a strict code generation engine. "
                    "You MUST return ONLY valid JSON. "
                    "No markdown. No explanations. No text outside JSON."
                )
            },
            {"role": "user", "content": prompt}
        ]
    )

    raw = response["message"]["content"]
    raw = clean_json(raw)

    try:
        return json.loads(raw)
    except Exception as e:
        return {
            "error": "invalid_json_from_llm",
            "exception": str(e),
            "raw": raw
        }

# -------- PROMPT ENGINE --------

def build_prompt(task: str, workspace_files: list = []) -> str:

    context = ""

    if workspace_files:
        context = "\n".join(workspace_files)

    return f"""
Return ONLY valid JSON.

You are operating inside a git worktree.

Workspace files (existing structure):
{context}

You MUST only modify, create or delete files inside this workspace.

You MUST NOT use paths outside this list unless creating new files logically inside the project structure.

You MUST follow this schema exactly:

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
- "operations" can be empty array if unsure

Task:
{task}
"""

# -------- API --------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/agent/run")
def run_agent(req: AgentRequest):

    REPO_ROOT = "/opt/agent-repos/agent-test-repo"

    # ----------------------------
    # 1. crear run aislado
    # ----------------------------
    run_id = str(uuid.uuid4())
    branch = f"agent-run-{run_id}"
    workspace = f"/tmp/agent-runs/{run_id}"

    subprocess.run(
        ["git", "worktree", "add", workspace, "-b", branch],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True
    )

    subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", workspace],
        capture_output=True,
        text=True,
        check=True
    )

    workspace_files = list_workspace_files(workspace)
    prompt = build_prompt(req.task, workspace_files)

    # ----------------------------
    # 2. llamar LLM
    # ----------------------------
    result = call_llm(prompt)

    if "operations" not in result:
        result = {
            "operations": [],
            "warnings": ["missing_operations"],
            "raw": result
        }

    operations = result.get("operations", [])

    # ----------------------------
    # 3. límites de seguridad
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
    # 4. ejecutar operaciones en WORKSPACE
    # ----------------------------
    execution = []

    for op in operations:
        execution.append(apply_operation(op, workspace))

    # ----------------------------
    # 5. diff sobre WORKSPACE
    # ----------------------------
    git_diff = get_git_diff(workspace)

    # ----------------------------
    # 6. respuesta
    # ----------------------------
    return {
        "run_id": run_id,
        "workspace": workspace,
        "llm_result": result,
        "execution": execution,
        "git_diff": git_diff
    }