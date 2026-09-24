"""F1/C3 — KEEP/DELETE operations never invent a MODIFY.

Pre-C3, the graph-viability anchor-preservation pass flipped a KEEP (or a
non-composition DELETE) into MODIFY just so the builder had a node, which made
delete-only/keep-only plans regenerate the page file. C3 removes that pass:
only contract-justified composition sync (child CREATE/DELETE → parent MODIFY)
may generate a MODIFY.
"""

import os
import shutil
import subprocess
import uuid

from app.config.settings import settings
from app.state.run_state import delete_run_state, save_run_state

from tests.e2e.helpers import build_plan_from_actions

PAGE_TSX = (
    "import React from 'react';\n"
    "import { Timeseries } from '../components/Timeseries';\n"
    "export const Page: React.FC = () => (\n"
    "  <div><Timeseries data={[]} /></div>\n"
    ");\n"
)
TIMESERIES_TSX = "import React from 'react';\nexport const Timeseries: React.FC = () => <div/>;\n"
KPI_ROW_TSX = "import React from 'react';\nexport const KpiRow: React.FC = () => <div/>;\n"


def _seed_workspace(run_id: str, files: list[tuple[str, str]]) -> str:
    ws = os.path.join(settings.RUNS_DIR, run_id)
    shutil.rmtree(ws, ignore_errors=True)
    for rel, content in files:
        full = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed", "--allow-empty"], cwd=ws, capture_output=True)
    return ws


def _apply(run_id: str, ws: str, actions: list[dict]) -> dict:
    from app.api.agent_apply import _agent_apply
    from app.contracts.apply_request import ApplyRequest

    plan = build_plan_from_actions(actions).to_dict()
    save_run_state(run_id, {
        "phase": "confirmed",
        "confirmed_intent": {
            "contract_id": "dashboard.sales_overview",
            "actions": [
                {"verb": a["verb"], "target_capability": a["target_capability"], "confidence": 0.9}
                for a in actions
            ],
        },
        "compiled_plan": plan,
        "plan_preview": {"summary_human": "test"},
        "gate": {"blocked": False},
    })
    try:
        return _agent_apply(ApplyRequest(
            run_id=run_id,
            plan=None,
            dry_run=True,
            confirmed_deletions=[a["target_capability"] for a in actions if a["verb"] == "remove"],
        ))
    finally:
        delete_run_state(run_id)
        shutil.rmtree(ws, ignore_errors=True)


class TestC3NoInventedModify:
    """F1/C3: KEEP/DELETE cannot invent a MODIFY, end to end."""

    def test_delete_page_materializes_delete_only(self):
        """DELETE layout.page → only a delete op; no create/modify ops."""
        seed = [
            ("src/pages/dashboard/Page.tsx", PAGE_TSX),
            ("src/components/Timeseries.tsx", TIMESERIES_TSX),
        ]
        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id, seed)
        result = _apply(run_id, ws, [{"verb": "remove", "target_capability": "layout.page"}])

        execution = result.get("execution", {})
        assert execution.get("status") == "ok", execution
        ops = execution.get("operations", [])
        assert ops, "expected a delete op"
        assert all(op["action"] == "delete" for op in ops), (
            f"KEEP/DELETE plan invented a create/modify op: {[(o['action'], o['path']) for o in ops]}"
        )
        assert any("Page.tsx" in op["path"] for op in ops)

    def test_delete_child_still_syncs_parent_composition(self):
        """DELETE a composition child → page MODIFY is contract-justified (kept)."""
        seed = [
            ("src/pages/dashboard/Page.tsx", PAGE_TSX),
            ("src/components/Timeseries.tsx", TIMESERIES_TSX),
            ("src/components/KpiRow.tsx", KPI_ROW_TSX),
        ]
        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id, seed)
        result = _apply(run_id, ws, [{"verb": "remove", "target_capability": "presentation.kpi_row"}])

        execution = result.get("execution", {})
        assert execution.get("status") == "ok", execution
        ops = execution.get("operations", [])
        paths = [(o["action"], o["path"]) for o in ops]
        assert any(o[0] == "delete" and "KpiRow.tsx" in o[1] for o in paths), paths
        assert any(o[0] == "modify" and "Page.tsx" in o[1] for o in paths), (
            f"child DELETE must promote parent MODIFY via composition sync: {paths}"
        )