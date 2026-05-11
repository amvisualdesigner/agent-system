from typing import Dict, Any
import json

from app.utils.state import write_state
from app.contracts.plan_request import PlanRequest

schema = """
{
  "steps": [
    {
      "path": "src/file.ts",
      "action": "create",
      "intent": "why this change is needed",
      "proposed_content": "optional final content"
    }
  ]
}
"""

def build_prompt(task: str, workspace_files: list = []) -> str:
    context = "\n".join(workspace_files) if workspace_files else ""
    return f"""
Return ONLY valid JSON.

You are a software planning engine.

Your task is to generate a semantic modification plan for a repository.

You DO NOT execute changes.

You ONLY describe intended modifications.

action MUST be one of: create, modify, delete

Schema:
{schema}

Rules:
- NO markdown
- NO explanations
- ONLY valid JSON
- describe intent clearly
- proposed_content is optional

Task:
{task}
"""

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
    

def generate_plan(req: PlanRequest, workspace_files):

    prompt = build_prompt(req.task, workspace_files)

    llm_result = call_llm(prompt)

    return llm_result