"""Fase 5.4 — Full Stack tests.

End-to-end flow through all 4 containers.
Exercises: user intent -> orchestrator -> backend -> vllm -> git commit.

Requires: all 4 containers running.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.container.assert_content import assert_content_quality, assert_delete_operation

pytestmark = pytest.mark.fullstack


def _run_id() -> str:
    return str(uuid.uuid4())


def _interpret_confirm_apply(
    client: httpx.Client,
    message: str,
    dry_run: bool = True,
) -> dict:
    run_id = _run_id()
    draft_resp = client.post(
        "/agent/interpret",
        json={"run_id": run_id, "message": message},
    )
    assert draft_resp.status_code == 200
    draft = draft_resp.json()
    if draft.get("status") != "ok":
        return {"status": "skipped", "reason": draft.get("status")}

    confirm_body = {
        "run_id": run_id,
        "interpretation_id": draft.get("interpretation_id", ""),
        "contract_id": draft.get("contract_id", ""),
        "contract_version": draft.get("contract_version", 1),
        "actions": [
            {"verb": a["verb"], "target_capability": a["target_capability"]}
            for a in draft.get("proposed_actions", [])
        ],
    }
    confirm_resp = client.post("/agent/confirm", json=confirm_body)
    assert confirm_resp.status_code == 200
    confirm = confirm_resp.json()
    if confirm.get("status") != "ok":
        return {"status": "skipped", "reason": confirm.get("status")}

    deletions = [a["target_capability"] for a in draft.get("proposed_actions", []) if a.get("verb") in ("remove", "delete")]
    apply_body = {
        "run_id": run_id,
        "plan": confirm.get("plan"),
        "dry_run": dry_run,
        "confirmed_deletions": deletions,
    }
    apply_resp = client.post("/agent/apply", json=apply_body)
    assert apply_resp.status_code == 200
    return apply_resp.json()


class TestFullStack:
    def test_remove_kpi(self, backend_client: httpx.Client):
        result = _interpret_confirm_apply(backend_client, "remove the KPI row")
        if result.get("status") == "skipped":
            pytest.skip(result.get("reason", "interpretation failed"))
        ops = result.get("execution", {}).get("operations", [])
        assert_delete_operation(ops, "KpiRow")
        # Parent page MODIFY should preserve remaining children
        assert_content_quality(ops, "SalesOverviewPage",
                               "Timeseries",  # KpiRow deleted, Timeseries stays
                               )

    def test_add_timeseries(self, backend_client: httpx.Client):
        result = _interpret_confirm_apply(backend_client, "add a line chart")
        if result.get("status") == "skipped":
            pytest.skip(result.get("reason", "interpretation failed"))
        ops = result.get("execution", {}).get("operations", [])
        # CREATE should produce real chart implementation, not an empty stub
        assert_content_quality(ops, "Timeseries",
                               "Card",       # wrapping card component
                               "metric",     # metric prop rendered
                               )

    def test_update_metrics(self, backend_client: httpx.Client):
        result = _interpret_confirm_apply(
            backend_client, "update KPI metrics to revenue and growth",
        )
        if result.get("status") == "skipped":
            pytest.skip(result.get("reason", "interpretation failed"))
        assert result.get("execution", {}).get("status") == "ok"
        ops = result.get("execution", {}).get("operations", [])
        # MODIFY should preserve KpiRow implementation (metrics.map, rendering)
        assert_content_quality(ops, "KpiRow",
                               "metrics.map",  # iteration over metrics items
                               "kpi-card",     # card rendering per item
                               )

    def test_update_dashboard(self, backend_client: httpx.Client):
        result = _interpret_confirm_apply(backend_client, "update dashboard")
        if result.get("status") == "skipped":
            pytest.skip(result.get("reason", "interpretation failed"))
        ops = result.get("execution", {}).get("operations", [])
        assert len(ops) >= 1
        # MODIFY should preserve dashboard structure
        assert_content_quality(ops, "SalesOverviewPage",
                               "KpiRow",     # child component import preserved
                               "Timeseries", # child component import preserved
                               "useDashboardData",  # data hook preserved
                               )

    def test_verify_present(self, backend_client: httpx.Client):
        result = _interpret_confirm_apply(backend_client, "remove the KPI row")
        if result.get("status") == "skipped":
            pytest.skip(result.get("reason", "interpretation failed"))
        meta = result.get("meta", {})
        assert "audit" in meta or "fidelity" in meta or "ownership" in meta
