from typing import Dict, Any
import json
import time
import logging
import atexit

import httpx
from app.utils.state import write_state
from app.contracts.plan_request import PlanRequest
from app.config.settings import settings
from app.semantic_engine import load_semantic_entries, retrieve, compile_context, is_semantic_task

logger = logging.getLogger(__name__)

schema = """
Skill execution (optional):
{
  "actions": [
    {
      "type": "use_skill",
      "target": "skill",
      "name": "dashboard.sales_overview",
      "params": {
        "metrics": ["revenue", "growth"],
        "timeseries_metric": "revenue"
      }
    }
  ]
}

File execution:
{
  "actions": [
    {
      "type": "create",
      "target": "file",
      "file_path": "src/file.ts",
      "description": "what this change does",
      "intent": "empty | scaffold"  # optional, default=empty
    }
  ]
}
"""

def build_prompt(task: str, workspace_files: list | None = None, semantic_context: str | None = None) -> str:
    if workspace_files is None:
        workspace_files = []
    context = "\n".join(workspace_files) if workspace_files else ""
    hint = ""
    if semantic_context:
        hint = (
            "\n- Semantic context is present as hints only, never binding. "
            "You decide the action type based on the task."
        )
    return f"""
You must output STRICT JSON only.

Rules:
- Output must be valid JSON parsable by json.loads
- Do NOT use markdown
- Do NOT use backticks (`) under any circumstance
- Do NOT include raw code (JS/TS/React) inside JSON strings
- If code is needed, represent it as structured data or description, not literal source code
- All strings must use escaped newlines (\\n), not template literals
- Maximum 10 actions in the plan. Focus on essential files only.
- Each scaffold operation has cost = 1. Total scaffold cost must be ≤ 3.
  Use intent "empty" for data/config files (cost = 0).
- If you cannot comply, return: {{"error": "invalid_plan"}}
{hint}

Violation of these rules makes the output invalid.

You are a software planning engine.
Your task is to generate a semantic modification plan for a repository.
You DO NOT execute changes.
You ONLY describe intended modifications.

Schema:
{schema}

# Semantic System Context
{semantic_context}

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

def _validate_plan(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {"error": "invalid_json_from_llm", "exception": "Response is not a dict", "raw": str(data)}

    if "error" in data and data["error"]:
        return data

    if "actions" not in data:
        return {"error": "invalid_json_from_llm", "exception": "Missing 'actions' key", "raw": json.dumps(data)}

    if not isinstance(data["actions"], list) or len(data["actions"]) == 0:
        return {"error": "invalid_json_from_llm", "exception": "'actions' must be a non-empty list", "raw": json.dumps(data)}

    for i, action in enumerate(data["actions"]):
        if not isinstance(action, dict):
            return {"error": "invalid_json_from_llm", "exception": f"Action {i} is not a dict", "raw": json.dumps(data)}
        if "type" not in action:
            return {"error": "invalid_json_from_llm", "exception": f"Action {i} missing 'type'", "raw": json.dumps(data)}

    return data


_http_client: httpx.Client | None = None


def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=90, trust_env=False)
    return _http_client


@atexit.register
def _close_http_client():
    global _http_client
    if _http_client is not None:
        _http_client.close()


def call_llm(prompt: str) -> Dict[str, Any]:
    url = f"{settings.LLM_BASE_URL}/v1/chat/completions"

    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict code generation engine. "
                    "Return ONLY valid JSON with this structure:\n"
                    "{ 'actions': [ { 'type': str, 'file_path': str, 'description': str } ] }\n"
                    "No markdown. No explanations. No text outside JSON."
                )
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 2048,
        "stop": None
    }

    headers = {"Content-Type": "application/json"}
    if settings.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

    client = _get_http_client()

    for attempt in range(3):
        attempt_start = time.time()
        try:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            latency = time.time() - attempt_start
            logger.info("vLLM request succeeded", extra={"attempt": attempt + 1, "latency": f"{latency:.2f}s"})

            raw_text = data["choices"][0]["message"]["content"]
            raw = clean_json(raw_text)

            try:
                parsed = json.loads(raw)
            except Exception as e:
                if attempt < 2:
                    logger.warning("JSON parse failed, retrying", extra={"attempt": attempt + 1, "error": str(e)})
                    continue
                return {"error": "invalid_json_from_llm", "exception": str(e), "raw": raw}

            return _validate_plan(parsed)

        except httpx.ConnectError:
            if attempt < 2:
                logger.warning("ConnectError, retrying", extra={"attempt": attempt + 1})
                time.sleep(1 * (attempt + 1))
                continue
            return {
                "error": "llm_connection_failed",
                "exception": f"Cannot connect to vLLM at {settings.LLM_BASE_URL}. Is the vLLM container running?"
            }
        except httpx.TimeoutException:
            if attempt < 2:
                logger.warning("Timeout, retrying", extra={"attempt": attempt + 1})
                continue
            return {
                "error": "llm_timeout",
                "exception": "LLM request timed out after 90 seconds (3 attempts)"
            }
        except httpx.HTTPStatusError as e:
            if attempt < 2 and e.response.status_code >= 500:
                logger.warning("HTTP error, retrying", extra={"attempt": attempt + 1, "status": e.response.status_code})
                time.sleep(1 * (attempt + 1))
                continue
            return {
                "error": "llm_http_error",
                "exception": f"LLM returned HTTP {e.response.status_code}: {e.response.text}"
            }
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            if attempt < 2:
                logger.warning("Response format error, retrying", extra={"attempt": attempt + 1, "error": str(e)})
                continue
            return {
                "error": "llm_invalid_response",
                "exception": f"Unexpected response format: {e}",
                "raw": response.text if 'response' in locals() else ""
            }


SEMANTIC_ENTRIES = load_semantic_entries(
    settings.SEMANTIC_DIR
)


def build_semantic_context(task: str) -> str:
    # include_as_hint_only = True  — context is advisory, never forced
    matches = retrieve(task, SEMANTIC_ENTRIES, top_k=5, min_score=0)
    return compile_context(matches)


def generate_plan(req: PlanRequest, workspace_files):

    semantic_context = build_semantic_context(req.task)

    prompt = build_prompt(req.task, workspace_files, semantic_context)

    llm_result = call_llm(prompt)

    return llm_result