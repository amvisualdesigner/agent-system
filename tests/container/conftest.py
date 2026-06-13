"""Pytest configuration for container-dependent tests (Fase 5).

Markers:
  - container:    requires Docker + compose (any service)
  - llm:          requires vllm container
  - backend_api:  requires backend + vllm containers
  - orchestrator: requires orchestrator + backend + vllm containers
  - fullstack:    requires all 4 containers

Fixtures:
  - docker_ok:   skip if Docker/compose unavailable
  - vllm_url:    base URL for vLLM API
  - backend_url: base URL for backend API
  - orch_url:    base URL for orchestrator API
  - ui_url:      base URL for UI
"""

from __future__ import annotations

import os
import sys

import pytest

# REPO_ROOT es requerido por backend/app/config/settings.py
os.environ.setdefault("REPO_ROOT", "/opt/agent-repos/agent-test-repo")

from tests.container.docker import (  # noqa: E402
    require_services,
    SERVICE_PORTS,
)

pytest_plugins = ["tests.container.client"]


# ── Markers ───────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line("markers", "container: requires Docker + compose (any service)")
    config.addinivalue_line("markers", "llm: requires vllm container")
    config.addinivalue_line("markers", "backend_api: requires backend + vllm containers")
    config.addinivalue_line("markers", "orchestrator: requires orchestrator + backend + vllm")
    config.addinivalue_line("markers", "fullstack: requires all 4 containers")


# ── Skip decorators ───────────────────────────────────────────

def _skip_when(service: str):
    def wrapper(func):
        return pytest.mark.skipif(
            require_services(service) is not None,
            reason=f"Container '{service}' is not available. Start with: docker compose up -d",
        )(func)
    return wrapper


skip_if_no_vllm = _skip_when("vllm")
skip_if_no_backend = _skip_when("backend")
skip_if_no_orchestrator = _skip_when("orchestrator")
skip_if_no_ui = _skip_when("ui")


# ── Auto-skip hook ────────────────────────────────────────────

def pytest_runtest_setup(item):
    markers_map = {
        "llm": "vllm",
        "backend_api": "backend",
        "orchestrator": "orchestrator",
        "fullstack": "ui",
    }
    for marker, service in markers_map.items():
        if item.get_closest_marker(marker):
            err = require_services(service)
            if err:
                pytest.skip(err)


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def docker_ok():
    err = require_services("vllm")
    if err:
        pytest.skip(err)


@pytest.fixture
def vllm_url() -> str:
    port = SERVICE_PORTS.get("vllm", 7000)
    return os.getenv("LLM_BASE_URL", f"http://localhost:{port}")


@pytest.fixture
def backend_url() -> str:
    port = SERVICE_PORTS.get("backend", 8000)
    return os.getenv("BACKEND_URL", f"http://localhost:{port}")


@pytest.fixture
def orch_url() -> str:
    port = SERVICE_PORTS.get("orchestrator", 9000)
    return os.getenv("ORCHESTRATOR_URL", f"http://localhost:{port}")


@pytest.fixture
def ui_url() -> str:
    port = SERVICE_PORTS.get("ui", 5173)
    return os.getenv("UI_URL", f"http://localhost:{port}")
