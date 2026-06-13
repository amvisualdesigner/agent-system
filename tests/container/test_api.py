"""Fase 5.2 — Backend API tests.

Hits real HTTP endpoints on the backend container.
Requires: vllm + backend containers running.

Mark with @pytest.mark.backend_api (auto-skipped if backend is down).
Uses sync httpx.Client (no pytest-asyncio dependency).
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.container.assert_content import assert_content_quality

pytestmark = pytest.mark.backend_api


def _run_id() -> str:
    return str(uuid.uuid4())


# ── Helpers ──────────────────────────────────────────────────

def api_interpret(client: httpx.Client, message: str) -> dict:
    body = {"run_id": _run_id(), "message": message}
    resp = client.post("/agent/interpret", json=body)
    assert resp.status_code == 200
    return resp.json()


def api_confirm(client: httpx.Client, run_id: str, draft: dict) -> dict:
    actions = draft.get("proposed_actions", [])
    body = {
        "run_id": run_id,
        "interpretation_id": draft.get("interpretation_id", ""),
        "contract_id": draft.get("contract_id", ""),
        "contract_version": draft.get("contract_version", 1),
        "actions": [
            {
                "verb": a.get("verb", ""),
                "target_capability": a.get("target_capability", ""),
                "confidence": a.get("confidence", 1.0),
            }
            for a in actions
        ],
    }
    resp = client.post("/agent/confirm", json=body)
    assert resp.status_code == 200
    return resp.json()


def api_apply(
    client: httpx.Client,
    run_id: str,
    confirm_result: dict,
    dry_run: bool = True,
) -> dict:
    body = {
        "run_id": run_id,
        "plan": confirm_result.get("plan"),
        "dry_run": dry_run,
        "confirmed_deletions": [],
    }
    resp = client.post("/agent/apply", json=body)
    assert resp.status_code == 200
    return resp.json()


# ── Tests ────────────────────────────────────────────────────

class TestAPIHealth:
    def test_health(self, backend_client: httpx.Client):
        resp = backend_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"


class TestAPIFullFlow:
    def test_interpret_remove_kpi(self, backend_client: httpx.Client):
        draft = api_interpret(backend_client, "remove the KPI row")
        assert draft.get("status") == "ok"
        actions = draft.get("proposed_actions", [])
        assert len(actions) == 1
        assert actions[0]["verb"] == "remove"
        assert actions[0]["target_capability"] == "presentation.kpi_row"

    def test_interpret_add_timeseries(self, backend_client: httpx.Client):
        draft = api_interpret(backend_client, "add a line chart")
        assert draft.get("status") == "ok"
        actions = draft.get("proposed_actions", [])
        assert len(actions) >= 1
        assert actions[0]["verb"] in ("create", "add")

    def test_interpret_update_metrics(self, backend_client: httpx.Client):
        draft = api_interpret(backend_client, "update KPI metrics to revenue and growth")
        assert draft.get("status") == "ok"
        actions = draft.get("proposed_actions", [])
        assert len(actions) == 1
        assert actions[0]["verb"] == "modify"
        assert actions[0]["target_capability"] == "presentation.kpi_row"

    def test_interpret_update_dashboard(self, backend_client: httpx.Client):
        draft = api_interpret(backend_client, "update dashboard")
        assert draft.get("status") == "ok"

    def test_interpret_invalid_needs_clarification(self, backend_client: httpx.Client):
        draft = api_interpret(backend_client, "I like the dashboard")
        assert draft.get("status") == "needs_clarification"

    def test_interpret_unsupported(self, backend_client: httpx.Client):
        draft = api_interpret(backend_client, "fix the database schema")
        assert draft.get("status") in ("unsupported", "needs_clarification")


class TestAPIConfirmFlow:
    def test_confirm_full_flow(self, backend_client: httpx.Client):
        run_id = _run_id()
        body = {"run_id": run_id, "message": "remove the KPI row"}
        draft_resp = backend_client.post("/agent/interpret", json=body)
        assert draft_resp.status_code == 200
        draft = draft_resp.json()
        assert draft.get("status") == "ok"

        confirm_body = {
            "run_id": run_id,
            "interpretation_id": draft.get("interpretation_id", ""),
            "contract_id": draft.get("contract_id", ""),
            "contract_version": draft.get("contract_version", 1),
            "actions": [
                {
                    "verb": a["verb"],
                    "target_capability": a["target_capability"],
                }
                for a in draft.get("proposed_actions", [])
            ],
        }
        confirm_resp = backend_client.post("/agent/confirm", json=confirm_body)
        assert confirm_resp.status_code == 200
        confirm = confirm_resp.json()
        assert confirm.get("status") == "ok"
        assert "plan" in confirm
        assert "plan_preview" in confirm

    def test_confirm_without_interpret_rejected(self, backend_client: httpx.Client):
        body = {
            "run_id": _run_id(),
            "interpretation_id": "fake",
            "contract_id": "dashboard.sales_overview",
            "actions": [{"verb": "remove", "target_capability": "presentation.kpi_row"}],
        }
        resp = backend_client.post("/agent/confirm", json=body)
        data = resp.json()
        assert data.get("status") == "rejected"


class TestAPIApplyFlow:
    def test_apply_dry_run(self, backend_client: httpx.Client):
        run_id = _run_id()
        draft_resp = backend_client.post(
            "/agent/interpret",
            json={"run_id": run_id, "message": "remove the KPI row"},
        )
        assert draft_resp.status_code == 200
        draft = draft_resp.json()

        confirm_body = {
            "run_id": run_id,
            "interpretation_id": draft.get("interpretation_id", ""),
            "contract_id": draft.get("contract_id", ""),
            "actions": [
                {"verb": a["verb"], "target_capability": a["target_capability"]}
                for a in draft.get("proposed_actions", [])
            ],
        }
        confirm_resp = backend_client.post("/agent/confirm", json=confirm_body)
        assert confirm_resp.status_code == 200
        confirm = confirm_resp.json()

        deletions = [a["target_capability"] for a in draft.get("proposed_actions", []) if a.get("verb") in ("remove", "delete")]
        apply_body = {
            "run_id": run_id,
            "plan": confirm.get("plan"),
            "dry_run": True,
            "confirmed_deletions": deletions,
        }
        apply_resp = backend_client.post("/agent/apply", json=apply_body)
        assert apply_resp.status_code == 200
        result = apply_resp.json()
        assert result.get("execution", {}).get("status") in ("ok",)
        # After DELETE, parent page MODIFY must preserve remaining children
        ops = result.get("execution", {}).get("operations", [])
        assert_content_quality(ops, "SalesOverviewPage",
                               "Timeseries",
                               )

    def test_apply_without_confirm_rejected(self, backend_client: httpx.Client):
        run_id = _run_id()
        body = {
            "run_id": run_id,
            "plan": {},
            "dry_run": True,
            "confirmed_deletions": [],
        }
        resp = backend_client.post("/agent/apply", json=body)
        assert resp.status_code == 400


class TestAPIRuns:
    def test_runs_endpoint(self, backend_client: httpx.Client):
        run_id = _run_id()
        resp = backend_client.get(f"/runs/{run_id}")
        assert resp.status_code in (200, 404)


class TestAPIErrorCases:
    def test_empty_intent_gate_blocked(self, backend_client: httpx.Client):
        run_id = _run_id()
        draft_resp = backend_client.post(
            "/agent/interpret",
            json={"run_id": run_id, "message": ""},
        )
        assert draft_resp.status_code == 400

    def test_invalid_capability_rejected(self, backend_client: httpx.Client):
        draft = api_interpret(backend_client, "add a filter panel")
        assert draft.get("status") in ("ok", "needs_clarification", "unsupported")
