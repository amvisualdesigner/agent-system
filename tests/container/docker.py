"""Docker detection and service healthchecks for container-dependent tests.

Functions:
  - docker_available()        → bool
  - compose_ps(service)       → bool (is service running?)
  - check_health(url)         → bool
  - require_services(*names)  → pytest skip-or-fail helper
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

COMPOSE_PROJECT = "agent-system"

SERVICE_PORTS: dict[str, int] = {
    "vllm": 7000,
    "backend": 8000,
    "orchestrator": 9000,
    "ui": 5173,
}

SERVICE_HEALTH_PATHS: dict[str, str] = {
    "vllm": "/health",
    "backend": "/health",
    "orchestrator": "/health",
    "ui": "/",
}

SERVICE_DEPENDENCIES: dict[str, list[str]] = {
    "vllm": [],
    "backend": ["vllm"],
    "orchestrator": ["backend"],
    "ui": [],
}

SERVICE_CONTAINER_NAMES: dict[str, str] = {
    "vllm": "vllm",
    "backend": "agent-backend",
    "orchestrator": "agent-orchestrator",
    "ui": "agent-ui",
}


def docker_available() -> bool:
    return shutil.which("docker") is not None


def service_is_running(service: str) -> bool:
    """Check if the container for a service is running via docker ps."""
    cname = SERVICE_CONTAINER_NAMES.get(service)
    if not cname:
        return False
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name={cname}",
             "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        return "Up" in result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_health(service: str) -> bool:
    """Hit the HTTP health endpoint of a service."""
    port = SERVICE_PORTS.get(service)
    path = SERVICE_HEALTH_PATHS.get(service)
    if not port or not path:
        return False
    url = f"http://localhost:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def require_services(*names: str) -> str | None:
    """Return error message if any service is unavailable, else None.

    Checks Docker availability → compose → running state → HTTP health.
    """
    if not docker_available():
        return "Docker is not installed"

    missing: list[str] = []
    unhealthy: list[str] = []

    for name in names:
        deps = SERVICE_DEPENDENCIES.get(name, [])
        for dep in deps:
            if dep not in names and dep not in missing and dep not in unhealthy:
                if not service_is_running(dep):
                    missing.append(dep)
        if not service_is_running(name):
            missing.append(name)
        elif not check_health(name):
            unhealthy.append(name)

    if missing:
        return f"Services not running: {', '.join(missing)}. Run: docker compose up -d"
    if unhealthy:
        return f"Services unhealthy: {', '.join(unhealthy)}"
    return None


def running_services() -> list[str]:
    """Return list of services currently running + healthy."""
    result: list[str] = []
    for svc in SERVICE_PORTS:
        if service_is_running(svc) and check_health(svc):
            result.append(svc)
    return result
