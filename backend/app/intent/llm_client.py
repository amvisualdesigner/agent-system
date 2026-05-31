"""Clean LLM client for IntentInterpreter.

Minimal wrapper over vLLM-compatible API.
Does NOT import legacy planner code.
"""

from __future__ import annotations

import json
import time
import logging
from typing import Any

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

_http_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        timeout = httpx.Timeout(90.0, connect=15.0)
        _http_client = httpx.Client(timeout=timeout)
    return _http_client


def _close_client() -> None:
    global _http_client
    if _http_client is not None:
        _http_client.close()
        _http_client = None


def llm_chat(prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
    """Call LLM chat completion. Retries up to 3 times on transient errors.

    Returns parsed JSON dict on success, or dict with "error" key on failure.
    """
    url = f"{settings.LLM_BASE_URL}/v1/chat/completions"
    if system_prompt is None:
        system_prompt = "You are a helpful assistant that returns valid JSON."

    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 2048,
    }

    headers = {"Content-Type": "application/json"}
    if settings.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

    client = _get_client()
    last_error: str | None = None

    for attempt in range(3):
        try:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
            cleaned = _clean_json(raw)
            parsed = json.loads(cleaned)
            return parsed
        except httpx.ConnectError as e:
            last_error = f"Connection failed: {e}"
            logger.warning("LLM connect error (attempt %d): %s", attempt + 1, e)
            time.sleep(1 * (attempt + 1))
        except httpx.TimeoutException as e:
            last_error = f"Timeout: {e}"
            logger.warning("LLM timeout (attempt %d): %s", attempt + 1, e)
        except httpx.HTTPStatusError as e:
            last_error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.warning("LLM HTTP error (attempt %d): %s", attempt + 1, e.response.status_code)
            if e.response.status_code < 500:
                break
            time.sleep(1 * (attempt + 1))
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            last_error = f"Parse error: {e}"
            logger.warning("LLM parse error (attempt %d): %s", attempt + 1, e)

    return {"error": last_error or "unknown_error"}


def _clean_json(text: str) -> str:
    text = text.strip()
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        text = text[first:last + 1]
    return text
