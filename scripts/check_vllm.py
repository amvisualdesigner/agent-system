#!/usr/bin/env python3
"""Verifica que vLLM está funcionando correctamente."""
import httpx
import sys
import json
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "http://localhost:8000"


def check_vllm():
    # 1. Verificar modelo cargado primero (detecta AWQ roto, partial boot)
    try:
        r = httpx.get(f"{BASE_URL}/v1/models", timeout=10)
        r.raise_for_status()
        models = r.json()["data"]
        model_ids = [m["id"] for m in models]
        logger.info("Loaded models: %s", model_ids)
        if "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ" not in model_ids:
            logger.error("Expected model not found")
            sys.exit(1)
    except Exception as e:
        logger.error("Could not list models: %s", e)
        sys.exit(1)

    # 2. Health check
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=10)
        assert r.status_code == 200, f"Health check failed: {r.status_code}"
        logger.info("vLLM health check passed")
    except Exception as e:
        logger.error("vLLM is not responding: %s", e)
        sys.exit(1)

    # 3. Probar generación real con medición de latencia
    payload = {
        "model": "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
        "messages": [
            {"role": "user", "content": "Return ONLY the word 'hello' (no markdown, no explanation)."}
        ],
        "temperature": 0.0,
        "max_tokens": 100
    }

    start = time.time()
    try:
        r = httpx.post(
            f"{BASE_URL}/v1/chat/completions",
            json=payload,
            timeout=30
        )
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        latency = time.time() - start
        tokens_used = data.get("usage", {}).get("total_tokens", "N/A")
        logger.info("Generation OK — %d chars in %.2fs, tokens=%s", len(content), latency, tokens_used)
        logger.info("Response: %s", content[:200])
    except Exception as e:
        logger.error("Generation failed: %s", e)
        sys.exit(1)

    logger.info("vLLM is ready!")
    return True


if __name__ == "__main__":
    check_vllm()
