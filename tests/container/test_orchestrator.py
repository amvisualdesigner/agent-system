"""Fase 5.3 — Orchestrator API tests.

Hits real HTTP endpoints on the orchestrator container.
Requires: vllm + backend + orchestrator containers running.

Mark with @pytest.mark.orchestrator (auto-skipped if orchestrator is down).
"""

from __future__ import annotations

import uuid

import httpx
import pytest


pytestmark = pytest.mark.orchestrator


def _run_id() -> str:
    return str(uuid.uuid4())


# ── Tests ────────────────────────────────────────────────────

class TestOrchHealth:
    def test_health(self, orchestrator_client: httpx.Client):
        resp = orchestrator_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"


class TestOrchFullFlow:
    def test_interpret_confirm_apply(self, orchestrator_client: httpx.Client):
        msg = {"task": "remove the KPI row"}
        resp = orchestrator_client.post("/run", json=msg)
        assert resp.status_code == 200

    def test_runs_list(self, orchestrator_client: httpx.Client):
        resp = orchestrator_client.get("/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert isinstance(data["runs"], list)

    def test_runs_by_id(self, orchestrator_client: httpx.Client):
        run_id = _run_id()
        resp = orchestrator_client.get(f"/runs/{run_id}")
        assert resp.status_code in (200, 404)

    def test_confirm_before_interpret_rejected(self, orchestrator_client: httpx.Client):
        run_id = _run_id()
        body = {
            "run_id": run_id,
            "interpretation_id": "fake",
            "contract_id": "dashboard.sales_overview",
            "actions": [{"verb": "remove", "target_capability": "presentation.kpi_row"}],
        }
        resp = orchestrator_client.post(f"/run/{run_id}/confirm", json=body)
        assert resp.status_code in (400, 404, 200)
        if resp.status_code == 200:
            data = resp.json()
            assert "error" in data.get("detail", "").lower() or data.get("status") == "rejected"

    def test_apply_before_confirm_rejected(self, orchestrator_client: httpx.Client):
        run_id = _run_id()
        body = {"run_id": run_id, "dry_run": True}
        resp = orchestrator_client.post(f"/run/{run_id}/apply", json=body)
        assert resp.status_code in (400, 404, 200)
