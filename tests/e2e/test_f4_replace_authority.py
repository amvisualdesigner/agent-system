"""S2·F4 E2E — identidad REPLACE y Plan inválido en apply_engine.

Cubre contra el repo real:
  · REPLACE: SubstitutionOp trazable a source_capability/target_capability
  · missing target_capability en Plan y en frame → INVALID_CONFIRMED_PLAN
  · target no expresable → MISSING_COMPONENT (sin sustitución materializable)
  · attach (WHERE) independiente del WHAT de REPLACE

Requisito: /opt/agent-repos/agent-test-repo debe existir.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid

import pytest

from app.config.settings import settings

AGENT_TEST_REPO = "/opt/agent-repos/agent-test-repo"


@pytest.fixture(scope="module")
def repo_copy():
    if not os.path.isdir(AGENT_TEST_REPO):
        pytest.skip("agent-test-repo not available at " + AGENT_TEST_REPO)
    tmpdir = tempfile.mkdtemp(prefix="f4_replace_", dir=settings.RUNS_DIR)
    shutil.copytree(os.path.join(AGENT_TEST_REPO, "frontend"),
                    os.path.join(tmpdir, "frontend"), symlinks=True)
    subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmpdir, capture_output=True)
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


def _frame_action(**kw) -> dict:
    base = {"verb": "replace", "object": "timeseries", "direct_object": "timeseries",
            "confidence": 1.0}
    base.update(kw)
    return base


def _plan(frame_actions: list[dict], plan_actions: list[dict] | None = None,
          intents: list[dict] | None = None) -> dict:
    fa = [dict(a) for a in frame_actions]
    pa = plan_actions if plan_actions is not None else [
        {k: v for k, v in a.items() if k in ("verb", "target_capability",
                                             "source_capability", "params", "confidence")}
        for a in frame_actions
    ]
    return {
        "contract_id": "dashboard.sales_overview",
        "skill_ir": {"contract_id": "dashboard.sales_overview", "version": 1,
                     "params": {"metrics": ["revenue"]}, "confidence": 1.0},
        "semantic_frame": {"actions": fa, "objects": [], "constraints": [],
                           "confidence": 1.0, "missing_info": []},
        "actions": [dict(a) for a in pa],
        "intents": intents or [],
    }


def _apply(workspace: str, plan: dict, dry_run: bool = True) -> dict:
    from app.engine.apply_engine import apply_engine
    from app.runtime.context import RunContext

    artifacts = tempfile.mkdtemp(prefix="f4_art_", dir=settings.ARTIFACTS_DIR)
    try:
        ctx = RunContext(run_id=str(uuid.uuid4()), base_dir=workspace,
                         workspace=workspace, artifacts=artifacts)
        return apply_engine(ctx.run_id, plan, ctx, dry_run=dry_run)
    finally:
        shutil.rmtree(artifacts, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
# REPLACE — identidad del Plan
# ═══════════════════════════════════════════════════════════════════


class TestReplaceIdentityEndToEnd:
    def test_substitution_identity_matches_confirmed_plan(self, repo_copy):
        """Plan: source=kpi_row (sustituida), target=timeseries (nueva)."""
        plan = _plan([_frame_action(
            reference="presentation.kpi_row",
            source_capability="presentation.kpi_row",
            target_capability="presentation.timeseries",
        )])
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex

        from app.contracts.skill_registry import get_contract
        from app.contracts.contract_resolution import ContractResolution
        from app.contracts.skill_ir import SkillIR
        from app.engine.reconciliation import reconcile
        from app.engine.structural_completion import complete_structure

        pd = plan["skill_ir"]
        sir = SkillIR(contract_id=pd["contract_id"], version=pd["version"],
                      params=pd["params"], confidence=pd["confidence"])
        sf = plan["semantic_frame"]
        ir = complete_structure(reconcile(sf, sir),
                                ContractResolution.from_skillir(
                                    sir, get_contract("dashboard.sales_overview", 1)),
                                get_contract("dashboard.sales_overview", 1), sf)
        assert [(s.source, s.target) for s in ir.substitution_ops] == [
            ("presentation.kpi_row", "presentation.timeseries"),
        ]

    def test_replace_does_not_materialize_lifecycle_ops(self, repo_copy):
        plan = _plan([_frame_action(
            reference="presentation.kpi_row",
            source_capability="presentation.kpi_row",
            target_capability="presentation.timeseries",
        )])
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        # REPLACE → KEEP: ninguna operación de lifecycle.
        assert [op for op in ex["operations"] if op["action"] != "modify"] == [], ex

    def test_attach_where_is_independent_of_replace_what(self, repo_copy):
        """Un attach en la acción REPLACE no materializa nada (WHERE≠WHAT)."""
        plan = _plan([_frame_action(
            reference="presentation.kpi_row",
            source_capability="presentation.kpi_row",
            target_capability="presentation.timeseries",
            attach={"target": {"capability": "layout.page",
                               "instance_label": "SalesOverviewPage"},
                    "kind": "container", "provenance": "candidate"},
        )])
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        # REPLACE → KEEP: sin materialización y sin binding de attach.
        assert ex["operations"] == [], ex


# ═══════════════════════════════════════════════════════════════════
# 5/6 — missing target_capability (Plan y frame)
# ═══════════════════════════════════════════════════════════════════


class TestMissingTargetCapability:
    def test_missing_in_plan_is_invalid_confirmed_plan(self, repo_copy):
        plan = _plan(
            [_frame_action(reference="presentation.kpi_row",
                           source_capability="presentation.kpi_row")],
            plan_actions=[{"verb": "replace",
                           "source_capability": "presentation.kpi_row",
                           "params": {}, "confidence": 1.0}],
        )
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] == "conflict", ex
        assert ex["conflict"] == "invalid_confirmed_plan", ex
        assert ex["operations"] == [], ex

    def test_missing_in_frame_is_invalid_confirmed_plan(self, repo_copy):
        """El frame pierde el WHAT aunque plan['actions'] lo traiga (item 2)."""
        plan = _plan(
            [_frame_action(reference="presentation.kpi_row",
                           source_capability="presentation.kpi_row")],
            plan_actions=[{"verb": "replace",
                           "source_capability": "presentation.kpi_row",
                           "target_capability": "presentation.timeseries",
                           "params": {}, "confidence": 1.0}],
        )
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] == "conflict", ex
        assert ex["conflict"] == "invalid_confirmed_plan", ex
        assert "frame action" in (ex.get("detail") or ""), ex
        assert ex["operations"] == [], ex

    def test_frame_plan_target_mismatch_is_invalid_confirmed_plan(self, repo_copy):
        plan = _plan(
            [_frame_action(reference="presentation.kpi_row",
                           source_capability="presentation.kpi_row",
                           target_capability="presentation.kpi_row")],
            plan_actions=[{"verb": "replace",
                           "source_capability": "presentation.kpi_row",
                           "target_capability": "presentation.timeseries",
                           "params": {}, "confidence": 1.0}],
        )
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["conflict"] == "invalid_confirmed_plan", ex
        assert "does not match" in (ex.get("detail") or ""), ex
        assert ex["operations"] == [], ex

    def test_blank_target_in_frame_is_invalid_confirmed_plan(self, repo_copy):
        plan = _plan(
            [_frame_action(reference="presentation.kpi_row",
                           source_capability="presentation.kpi_row",
                           target_capability="   ")],
        )
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["conflict"] == "invalid_confirmed_plan", ex
        assert ex["operations"] == [], ex


# ═══════════════════════════════════════════════════════════════════
# 7 — target no expresable
# ═══════════════════════════════════════════════════════════════════


class TestNonexpressibleReplaceTarget:
    def test_unknown_target_conflicts_without_substitution(self, repo_copy):
        plan = _plan([_frame_action(
            reference="presentation.kpi_row",
            source_capability="presentation.kpi_row",
            target_capability="presentation.chart.pie",
        )], intents=[{"id": "i1", "capability": "presentation.chart.pie"}])
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] == "conflict", ex
        assert ex["conflict"] == "missing_component", ex
        assert ex["operations"] == [], ex

    def test_unknown_source_does_not_reinterpret_target(self, repo_copy):
        """Un source no expresable no altera el WHAT confirmado."""
        plan = _plan([_frame_action(
            reference="capability.inexistente",
            source_capability="capability.inexistente",
            target_capability="presentation.timeseries",
        )], intents=[{"id": "i1", "capability": "presentation.timeseries"}])
        ex = _apply(repo_copy, plan)["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        assert not any(op["action"] == "create" for op in ex["operations"]), ex