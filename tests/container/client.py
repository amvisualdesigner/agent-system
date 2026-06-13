"""HTTP client fixtures for container-dependent tests.

Fixtures:
  - http_client:            bare httpx.Client (sync)
  - backend_client:         httpx.Client pointing at backend API
  - orchestrator_client:    httpx.Client pointing at orchestrator API

Note: URL fixtures (backend_url, orch_url) are defined in conftest.py.
Uses sync httpx.Client to avoid pytest-asyncio dependency.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def http_client():
    with httpx.Client(timeout=30.0) as client:
        yield client


@pytest.fixture
def backend_client(backend_url: str):
    with httpx.Client(base_url=backend_url, timeout=30.0) as client:
        yield client


@pytest.fixture
def orchestrator_client(orch_url: str):
    with httpx.Client(base_url=orch_url, timeout=30.0) as client:
        yield client
