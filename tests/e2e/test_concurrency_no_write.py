"""F4 concurrency (option C) — stale workspace state blocks materialization.

Single materialization flow lock:
  1. /agent/confirm captures the worktree snapshot the confirmed plan/preview
     is based on and persists it.
  2. /agent/apply re-fingerprints the worktree BEFORE applying. If anything
     changed → NO WRITE (zero FileOps), status=conflict, reason=concurrency,
     phase reverted to confirmed. No merge, no reinterpretation.
"""

import os
import shutil
import subprocess
import uuid

from app.config.settings import settings
from app.state.run_state import delete_run_state, load_run_state, save_run_state

from tests.e2e.helpers import build_plan_from_actions


def _seed_workspace(run_id: str) -> str:
    ws = os.path.join(settings.RUNS_DIR, run_id)
    shutil.rmtree(ws, ignore_errors=True)
    for d in ("src/pages/dashboard", "src/components"):
        os.makedirs(os.path.join(ws, d), exist_ok=True)
    for rel, content in (
        ("src/pages/dashboard/Page.tsx",
         "import React from 'react';\nexport const Page: React.FC = () => <div/>;\n"),
        ("src/components/LineChart.tsx",
         "import React from 'react';\nexport const LineChart: React.FC = () => <svg/>;\n"),
    ):
        with open(os.path.join(ws, rel), "w") as f:
            f.write(content)
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed", "--allow-empty"], cwd=ws, capture_output=True)
    return ws


def _confirmed_state(run_id: str, ws: str) -> None:
    from app.engine.worktree_snapshot import snapshot_worktree
    save_run_state(run_id, {
        "phase": "confirmed",
        "confirmed_intent": {
            "contract_id": "dashboard.sales_overview",
            "actions": [{"verb": "create", "target_capability": "presentation.kpi_row", "confidence": 1.0}],
        },
        "compiled_plan": build_plan_from_actions(
            [{"verb": "create", "target_capability": "presentation.kpi_row"}]
        ).to_dict(),
        "plan_preview": {"summary_human": "create kpi_row"},
        "gate": {"blocked": False},
        "apply_snapshot": snapshot_worktree(ws),
    })


class TestConcurrencyNoWrite:
    """F4 option C: changed workspace after confirm → NO WRITE, zero FileOps."""

    def test_unchanged_workspace_applies(self):
        from app.api.agent_apply import _agent_apply
        from app.contracts.apply_request import ApplyRequest

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        _confirmed_state(run_id, ws)
        try:
            result = _agent_apply(ApplyRequest(run_id=run_id, dry_run=True))
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

        execution = result.get("execution", {})
        assert execution.get("status") == "ok", execution
        assert result.get("meta", {}).get("concurrency", {}).get("status") == "ok"

    def test_stale_workspace_blocks_without_write(self):
        from app.api.agent_apply import _agent_apply
        from app.contracts.apply_request import ApplyRequest

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        _confirmed_state(run_id, ws)

        target = os.path.join(ws, "src", "pages", "dashboard", "Page.tsx")
        original = open(target).read()
        with open(target, "a") as f:
            f.write("\n// concurrent edit\n")

        try:
            result = _agent_apply(ApplyRequest(run_id=run_id, dry_run=False))
            state = load_run_state(run_id)
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

        execution = result.get("execution", {})
        assert execution.get("status") == "conflict", result
        assert execution.get("reason") == "concurrency", execution
        assert execution.get("operations") == []
        assert execution.get("diff") is None
        # Phase reverted to confirmed: the user can restart (re-plan) or cancel.
        assert state.get("phase") == "confirmed"

    def test_stale_workspace_leaves_files_untouched(self):
        from app.api.agent_apply import _agent_apply
        from app.contracts.apply_request import ApplyRequest

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        _confirmed_state(run_id, ws)

        target = os.path.join(ws, "src", "pages", "dashboard", "Page.tsx")
        with open(target, "a") as f:
            f.write("\n// concurrent edit\n")

        def _read_tree(root: str) -> dict:
            out = {}
            for base, _dirs, files in os.walk(root):
                for fn in files:
                    rel = os.path.relpath(os.path.join(base, fn), root)
                    if ".git" in rel.split(os.sep):
                        continue
                    with open(os.path.join(base, fn), encoding="utf-8") as f:
                        out[rel] = f.read()
            return out

        before = _read_tree(ws)
        try:
            _agent_apply(ApplyRequest(run_id=run_id, dry_run=False))
            after = _read_tree(ws)
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

        # Only the externally-introduced edit exists; NO plan write was made.
        assert after.keys() == before.keys()
        for rel, content in before.items():
            assert after[rel] == content, f"unexpected write to {rel}"