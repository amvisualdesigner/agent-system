"""F1/D2 — /agent/apply cannot substitute the Confirmed Plan via req.plan.

The Confirmed Plan persisted at /agent/confirm (state["compiled_plan"]) is the
only lifecycle authority. A request body carrying a divergent `plan` must be
ignored: the apply must materialize exactly the confirmed plan's footprint.
"""

import os
import shutil
import subprocess
import uuid

from app.config.settings import settings
from app.state.run_state import delete_run_state, save_run_state

from tests.e2e.helpers import build_plan_from_actions

# Actions used to build the two plans (Confirmed vs adversarial request plan).
#  - Confirmed: CREATE presentation.kpi_row → KpiRow.tsx (does not exist in the
#    seeded workspace, so Gate 2.5b passes).
#  - Request-plan (adversarial): CREATE layout.page → already exists in the
#    seeded workspace; if honored it would CONFLICT / never create KpiRow.
CONFIRMED_ACTIONS = [{"verb": "create", "target_capability": "presentation.kpi_row"}]
ADVERSARIAL_ACTIONS = [{"verb": "create", "target_capability": "layout.page"}]

PAGE_TSX = "import React from 'react';\nexport const Page: React.FC = () => <div/>;\n"
LINECHART_TSX = "import React from 'react';\nexport const LineChart: React.FC = () => <svg/>;\n"


def _seed_workspace(run_id: str) -> str:
    """Create a git workspace for run_id inside RUNS_DIR (API reuse path)."""
    ws = os.path.join(settings.RUNS_DIR, run_id)
    shutil.rmtree(ws, ignore_errors=True)
    for d in ("src/pages/dashboard", "src/components"):
        os.makedirs(os.path.join(ws, d), exist_ok=True)
    for rel, content in (
        ("src/pages/dashboard/Page.tsx", PAGE_TSX),
        ("src/components/LineChart.tsx", LINECHART_TSX),
    ):
        with open(os.path.join(ws, rel), "w") as f:
            f.write(content)
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed", "--allow-empty"], cwd=ws, capture_output=True)
    return ws


def _confirmed_state(run_id: str, confirmed_plan: dict) -> None:
    save_run_state(run_id, {
        "phase": "confirmed",
        "confirmed_intent": {
            "contract_id": "dashboard.sales_overview",
            "actions": [
                {"verb": "create", "target_capability": "presentation.kpi_row", "confidence": 1.0},
            ],
        },
        "compiled_plan": confirmed_plan,
        "plan_preview": {"summary_human": "create kpi_row"},
        "gate": {"blocked": False},
    })


class TestD2ApplyPlanAuthority:
    """F1/D2: apply must use state["compiled_plan"], never req.plan."""

    def test_apply_ignores_divergent_request_plan(self):
        from app.api.agent_apply import _agent_apply
        from app.contracts.apply_request import ApplyRequest

        confirmed_plan = build_plan_from_actions(CONFIRMED_ACTIONS).to_dict()
        adversarial_plan = build_plan_from_actions(ADVERSARIAL_ACTIONS).to_dict()

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        _confirmed_state(run_id, confirmed_plan)
        try:
            result = _agent_apply(
                ApplyRequest(run_id=run_id, plan=adversarial_plan, dry_run=True)
            )
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

        execution = result.get("execution", {})
        assert execution.get("status") == "ok", (
            f"D2 violated: a divergent req.plan substituted the Confirmed Plan "
            f"(got status={execution.get('status')!r}, reason={execution.get('reason')!r}). "
            "Apply must materialize state['compiled_plan'], never req.plan."
        )
        ops = execution.get("operations", [])
        assert any(op["action"] == "create" and op["path"].endswith("KpiRow.tsx") for op in ops), (
            "Apply did not materialize the Confirmed Plan footprint (expected CREATE KpiRow.tsx)."
        )
        assert not any(op["action"] == "create" and op["path"].endswith("Page.tsx") for op in ops), (
            "The adversarial req.plan (CREATE layout.page) was honored — D2 broken."
        )

    def test_apply_without_request_plan_uses_confirmed_plan(self):
        from app.api.agent_apply import _agent_apply
        from app.contracts.apply_request import ApplyRequest

        confirmed_plan = build_plan_from_actions(CONFIRMED_ACTIONS).to_dict()

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        _confirmed_state(run_id, confirmed_plan)
        try:
            result = _agent_apply(ApplyRequest(run_id=run_id, plan=None, dry_run=True))
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

        execution = result.get("execution", {})
        assert execution.get("status") == "ok", execution
        ops = execution.get("operations", [])
        assert any(op["action"] == "create" and op["path"].endswith("KpiRow.tsx") for op in ops)