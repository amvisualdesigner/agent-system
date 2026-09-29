"""F5 phase lock — dry_run is preview-only (ZERO writes) and the effective
preview FileOps equal the FileOps a real apply materializes.

Single materialization flow invariants:
  1. dry_run generates the complete effective FileOps but performs zero writes
     (the terminal applier is the ONLY write point, gated by not dry_run).
  2. Generation is deterministic: the dry_run operations (shown to the user at
     confirm) are byte-identical to the operations the real apply writes.
     → preview == effective applied scope.
"""

import os
import shutil
import subprocess
import uuid

from app.runtime.context import build_context
from app.state.run_state import delete_run_state, save_run_state

from tests.e2e.helpers import build_plan_from_actions


def _seed_workspace(run_id: str) -> str:
    ws = os.path.join(settings_path(), run_id)
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


def settings_path() -> str:
    from app.config.settings import settings
    return settings.RUNS_DIR


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


def _plan_dict() -> dict:
    return build_plan_from_actions(
        [{"verb": "create", "target_capability": "presentation.kpi_row"}]
    ).to_dict()


class TestDryRunNoWrite:
    def test_dry_run_generates_ops_but_writes_nothing(self):
        from app.api.agent_apply import _agent_apply
        from app.contracts.apply_request import ApplyRequest

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        save_run_state(run_id, {
            "phase": "confirmed",
            "confirmed_intent": {
                "contract_id": "dashboard.sales_overview",
                "actions": [{"verb": "create", "target_capability": "presentation.kpi_row", "confidence": 1.0}],
            },
            "compiled_plan": _plan_dict(),
            "gate": {"blocked": False},
        })
        before = _read_tree(ws)
        try:
            result = _agent_apply(ApplyRequest(run_id=run_id, dry_run=True))
            after = _read_tree(ws)
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

        execution = result.get("execution", {})
        assert execution.get("status") == "ok", result
        # It MUST generate the effective operations…
        assert execution.get("operations"), "dry_run produced no operations"
        assert any(op.get("path", "").endswith("KpiRow.tsx") for op in execution["operations"])
        # …but it MUST NOT write a single byte.
        assert after == before, "dry_run performed writes"

    def test_real_apply_writes_exactly_the_operations(self):
        from app.engine.apply_engine import apply_engine

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        plan = _plan_dict()
        ctx = build_context(run_id)
        seed_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ws, capture_output=True, text=True
        ).stdout.strip()
        try:
            applied = apply_engine(
                run_id, plan, ctx, dry_run=False, confirmed_deletions=[]
            )
            ops = applied.get("execution", {}).get("operations", [])
            assert applied.get("execution", {}).get("status") == "ok", applied
            assert ops

            # Workspace changes since the seed commit must be EXACTLY the applied
            # FileOps targets. Provenance: no earlier stage may write, and dry_run
            # preview writes nothing — this locks the single-terminal-write-point
            # invariant.
            # S2 Fase 1 / L1: the State-Layer memory evidence file is git-isolated
            # (`.opencode/.gitignore`), so it never appears in the git diff; it
            # still persists on disk with its exact policy. The `.gitignore`
            # anchor itself appears exactly once (bootstrap scaffold), never the
            # memory file.
            changed = subprocess.run(
                ["git", "diff", "--name-only", seed_commit], cwd=ws,
                capture_output=True, text=True,
            ).stdout.splitlines()
            expected = {op.get("path") for op in ops} | {".opencode/.gitignore"}
            assert set(changed) == expected, (
                f"changes {sorted(set(changed))} != ops {sorted(expected)}"
            )
            assert ".opencode/semantic_memory.json" not in changed, (
                "memory evidence must never be part of git changes"
            )
            mem_path = os.path.join(ws, ".opencode", "semantic_memory.json")
            assert os.path.exists(mem_path), "memory evidence still persists on disk"
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

    def test_preview_fileops_equals_applied_fileops(self):
        from app.engine.apply_engine import apply_engine

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        plan = _plan_dict()
        ctx = build_context(run_id)
        try:
            preview = apply_engine(
                run_id, plan, ctx, dry_run=True, confirmed_deletions=[]
            )
            applied = apply_engine(
                run_id, plan, ctx, dry_run=False, confirmed_deletions=[]
            )
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

        preview_ops = preview.get("execution", {}).get("operations", [])
        applied_ops = applied.get("execution", {}).get("operations", [])
        assert preview.get("execution", {}).get("status") == "ok", preview
        assert applied.get("execution", {}).get("status") == "ok", applied
        assert preview_ops and applied_ops
        # Deterministic generation → the confirm-time preview IS the applied scope.
        assert preview_ops == applied_ops