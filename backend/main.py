from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any
import json
import ollama

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

def build_prompt(task: str) -> str:
    return f"""
Return ONLY valid JSON.

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

    prompt = build_prompt(req.task)

    result = call_llm(prompt)

    # fallback defensivo
    if "operations" not in result:
        result = {
            "operations": [],
            "warnings": ["missing_operations"],
            "raw": result
        }

    operations = result.get("operations", [])

    delete_ops = [op for op in operations if op.get("type") == "delete"]

    if len(delete_ops) > MAX_DELETES:
        return {
            "error": "too_many_deletes",
            "limit": MAX_DELETES,
            "received": len(delete_ops)
        }

    if len(operations) > MAX_OPERATIONS:
        return {"error": "too_many_operations"}

    execution = []

    for op in operations:
        execution.append(apply_operation(op))

    git_diff = get_git_diff()

    return {
        "llm_result": result,
        "execution": execution,
        "git_diff": git_diff
    }