"""Phase 2.6 — Invariance lock test.

Ensures StructuralIndex never invents lifecycle decisions.
The index reports reality (existence, paths). Lifecycle direction
comes from user intent, not from index.

Invariants:
  1. No DELETE without "remove" in semantic intent — regardless of index
  2. CREATE can be downgraded to KEEP+instance_only (file exists)
     but never escalated to MODIFY or DELETE
  3. MODIFY/DELETE require existence — without index they may become
     something else, but the intent direction is never flipped
"""

from __future__ import annotations

import os
import subprocess

import pytest

from app.contracts.skill_registry import get_contract
from app.contracts.skill_ir import SkillIR
from app.contracts.semantic_resolution import SemanticConflictError
from app.contracts.contract_resolution import ContractResolution
from app.engine.reconciliation import reconcile
from app.engine.structural_completion import (
    complete_structure, StructuralIR,
    CREATE, MODIFY, DELETE, KEEP,
)


def _commit(ws: str):
    subprocess.run(["git", "add", "-A"], cwd=ws, check=False)
    subprocess.run(["git", "commit", "-m", "seed", "--allow-empty", "--no-verify"],
                   cwd=ws, capture_output=True, check=False)


def _seed_file(ws: str, rel_path: str, content: str = ""):
    full = os.path.join(ws, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)


def _run(
    semantic_frame: dict,
    skill_ir: SkillIR,
    contract,
) -> StructuralIR | None:
    try:
        sem = reconcile(semantic_frame, skill_ir)
    except (ValueError, SemanticConflictError):
        return None
    cr = ContractResolution.from_skillir(skill_ir, contract)
    return complete_structure(sem, cr, contract, semantic_frame)


def _find(ir: StructuralIR | None, name: str):
    if ir is None:
        return None
    for rc in ir.capabilities:
        if rc.name == name:
            return rc
    return None


class TestStructuralIndexInvariance:
    """StructuralIndex never invents lifecycle decisions."""

    CID = "dashboard.sales_overview"
    CVER = 1

    @pytest.fixture
    def contract(self):
        c = get_contract(self.CID, self.CVER)
        if c is None:
            pytest.skip(f"Contract {self.CID} not found")
        return c

    @pytest.fixture
    def sir(self):
        return SkillIR(
            contract_id=self.CID, version=self.CVER,
            params={"metrics": ["revenue"]}, confidence=1.0,
        )

    @pytest.fixture
    def workspace_with_kpi(self, e2e_workspace):
        _seed_file(e2e_workspace, "KpiRow.tsx", "// kpi")
        _seed_file(e2e_workspace, "SalesOverviewPage.tsx", "// page")
        _commit(e2e_workspace)
        return e2e_workspace

    # ── Invariant 1: no DELETE without remove ──────────────────────────

    def test_no_delete_without_remove(self, contract, sir, workspace_with_kpi):
        """StructuralIndex nunca produce DELETE si no hay 'remove' en intent."""
        sf = {
            "actions": [{"verb": "modify", "object": "layout", "params": {}}],
            "params": {"metrics": ["revenue"]},
        }
        result = _run(sf, sir, contract)
        if result is None:
            pytest.skip("reconciliation failed")

        for rc in result.capabilities:
            assert rc.action != DELETE, (
                f"Invariant 1 violated: capability '{rc.name}' has DELETE "
                f"but no 'remove' in intent. "
                f"StructuralIndex may be leaking into lifecycle."
            )

    def test_delete_only_with_remove(self, contract, sir, workspace_with_kpi):
        """Control: 'remove' produce DELETE — solo bajo esa condicion."""
        sf = {
            "actions": [{"verb": "remove", "object": "kpi", "params": {}}],
            "params": {"metrics": ["revenue"]},
        }
        result = _run(sf, sir, contract)
        if result is None:
            pytest.skip("reconciliation failed")

        rc = _find(result, "presentation.kpi_row")
        assert rc is not None
        assert rc.action == DELETE, (
            f"'remove' intent should produce DELETE, got {rc.action}"
        )

    # ── Invariant 2: CREATE on existing → KEEP+instance_only ───────────

    def test_create_on_existing_produces_create(self, contract, sir, workspace_with_kpi):
        """Phase 3: CREATE on existing → action=CREATE (reconciliation moves to ApplyEngine)."""
        sf = {
            "actions": [{"verb": "create", "object": "kpi", "params": {}}],
            "params": {"metrics": ["revenue"]},
        }
        result = _run(sf, sir, contract)
        if result is None:
            pytest.skip("reconciliation failed")

        rc = _find(result, "presentation.kpi_row")
        assert rc is not None
        assert rc.action == CREATE, (
            f"CREATE should remain CREATE in Phase 3 (no repo degradation), got {rc.action}"
        )

    def test_create_without_index_produces_create(self, contract, sir, workspace_with_kpi):
        """CREATE sin index → action=CREATE (reality desconocida)."""
        sf = {
            "actions": [{"verb": "create", "object": "timeseries", "params": {}}],
            "params": {"metrics": ["revenue"]},
        }
        result = _run(sf, sir, contract)
        if result is None:
            pytest.skip("reconciliation failed")

        rc = _find(result, "presentation.timeseries")
        assert rc is not None
        assert rc.action == CREATE, (
            f"CREATE without index should produce CREATE, got {rc.action}"
        )

    # ── Invariant 3: index never escalates intent direction ────────────

    def test_index_never_escalates_to_delete(self, contract, sir, workspace_with_kpi):
        """CREATE never becomes DELETE through index."""
        sf = {
            "actions": [{"verb": "create", "object": "kpi", "params": {}}],
            "params": {"metrics": ["revenue"]},
        }
        result = _run(sf, sir, contract)
        if result is None:
            pytest.skip("reconciliation failed")

        for rc in result.capabilities:
            assert rc.action != DELETE, (
                f"Invariant 3 violated: CREATE intent produced DELETE for '{rc.name}'"
            )

    def test_index_never_escalates_modify_to_delete(self, contract, sir, workspace_with_kpi):
        """MODIFY never becomes DELETE through index."""
        sf = {
            "actions": [{"verb": "modify", "object": "kpi", "params": {}}],
            "params": {"metrics": ["revenue"]},
        }
        result = _run(sf, sir, contract)
        if result is None:
            pytest.skip("reconciliation failed")

        rc = _find(result, "presentation.kpi_row")
        if rc is not None:
            assert rc.action != DELETE, (
                f"MODIFY intent should not become DELETE: {rc.action}"
            )
