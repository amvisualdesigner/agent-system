from typing import Dict, Any
import json
import time
import logging
import atexit

import httpx
from app.utils.state import write_state
from app.contracts.plan_request import PlanRequest
from app.config.settings import settings

logger = logging.getLogger(__name__)

schema = """
{
  "actions": [
    {
      "type": "create",
      "file_path": "src/file.ts",
      "description": "why this change is needed",
      "content": "optional final content"
    }
  ]
}
"""

def build_prompt(task: str, workspace_files: list | None = None) -> str:
    if workspace_files is None:
        workspace_files = []
    context = "\n".join(workspace_files) if workspace_files else ""
    return f"""
Return ONLY valid JSON.

You are a software planning engine.

Your task is to generate a semantic modification plan for a repository.

You DO NOT execute changes.

You ONLY describe intended modifications.

action MUST be one of: create, modify, delete
YOU MUST output ONLY "actions".
DO NOT use "steps".

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
        for key in ("type", "file_path", "description"):
            if key not in action:
                return {"error": "invalid_json_from_llm", "exception": f"Action {i} missing '{key}'", "raw": json.dumps(data)}

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
        "max_tokens": 512,
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


def generate_plan(req: PlanRequest, workspace_files):

    prompt = build_prompt(req.task, workspace_files)

    llm_result = call_llm(prompt)

    return llm_result