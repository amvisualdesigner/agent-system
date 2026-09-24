"""F1/C4 — Anchor resolution is STRICTLY PHYSICAL, end to end.

The anchor decision must follow the hard matrix:
  forced (plan-specified) → use it
  0 physical candidates    → CONFLICT (anchor_ambiguity, blocking)
  1 physical candidate     → use it
  N physical candidates    → CONFLICT unless the plan specified one

No semantic scoring may re-rank candidates. Orphan components (no
composition parent in the contract, e.g. FilterPanel in analytics.filter)
are mounted into existing page container files; 0/N of them is a blocking
conflict surfaced as clarification_needed, never a silent guess.

Each test exercises apply_engine() (GraphIR → renderer → Anchor Resolution)
against a copy of the real agent-test-repo.
"""

import os
import shutil
import subprocess
import tempfile
import uuid

import pytest

from app.config.settings import settings
from app.intent.plan_compiler import compile_plan
from app.intent.models import ConfirmedIntent, IntentAction

AGENT_TEST_REPO = "/opt/agent-repos/agent-test-repo"

SECOND_PAGE = (
    "import React from 'react';\n"
    "export const AnalyticsOverviewPage: React.FC = () => (\n"
    "  <div className=\"analytics\">\n"
    "    <h1>Analytics</h1>\n"
    "  </div>\n"
    ");\n"
)


@pytest.fixture
def phase6_repo_copy():
    """Copy of agent-test-repo + v3 data_access.json (like test_real_repo_integration).

    Function-scoped: apply_engine dry_run=True still writes files to the
    workspace (it only skips the git commit), so a shared module fixture
    would mutate across tests.
    """
    if not os.path.isdir(AGENT_TEST_REPO):
        pytest.skip("agent-test-repo not available at " + AGENT_TEST_REPO)

    tmpdir = tempfile.mkdtemp(prefix="c4_anchor_", dir=settings.RUNS_DIR)
    shutil.copytree(
        os.path.join(AGENT_TEST_REPO, "frontend"),
        os.path.join(tmpdir, "frontend"),
        symlinks=True,
        ignore_dangling_symlinks=True,
    )
    opencode_dir = os.path.join(tmpdir, ".opencode")
    os.makedirs(opencode_dir, exist_ok=True)
    shutil.copy2(
        os.path.join(os.path.dirname(__file__), "..", "..", "backend", "config", "data_access.json"),
        os.path.join(opencode_dir, "data_access.json"),
    )

    subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)

    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


PAGE_REL = os.path.join("frontend", "src", "pages", "dashboard", "SalesOverviewPage.tsx")
SECOND_PAGE_REL = os.path.join("frontend", "src", "pages", "analytics", "AnalyticsOverviewPage.tsx")


def _filter_panel_plan() -> dict:
    """Plan for CREATE analytics.filter / presentation.filter_panel (orphan)."""
    confirmed = ConfirmedIntent(
        contract_id="analytics.filter",
        contract_version=1,
        actions=[
            IntentAction(
                verb="create",
                target_capability="presentation.filter_panel",
                params={"filters": ["region"]},
            ),
        ],
        params={"filters": ["region"]},
        user_message="Add a filter panel for region",
        interpretation_id="c4-anchor-matrix",
    )
    return compile_plan(confirmed).to_dict()


def _apply(workspace: str, forced_anchor_path: str | None = None) -> dict:
    from app.engine.apply_engine import apply_engine
    from app.runtime.context import RunContext

    artifacts_tmp = tempfile.mkdtemp(prefix="c4_artifact_", dir=settings.ARTIFACTS_DIR)
    ctx = RunContext(
        run_id=str(uuid.uuid4()),
        base_dir=workspace,
        workspace=workspace,
        artifacts=artifacts_tmp,
    )
    try:
        return apply_engine(
            ctx.run_id, _filter_panel_plan(), ctx,
            dry_run=True, forced_anchor_path=forced_anchor_path,
        )
    finally:
        shutil.rmtree(artifacts_tmp, ignore_errors=True)


class TestPhysicalAnchorMatrix:
    """forced → use; 0 → CONFLICT; 1 → use; N → CONFLICT (unless forced)."""

    def test_one_candidate_is_mounted(self, phase6_repo_copy):
        """Existing repo has exactly ONE page container → single candidate → mount."""
        result = _apply(phase6_repo_copy)
        ex = result["execution"]
        assert ex["status"] in ("ok", "verify_failed"), ex
        ops = ex["operations"]
        assert any(op["action"] == "create" and "FilterPanel" in op["path"] for op in ops), ops
        assert any(op["action"] == "modify" and PAGE_REL in op["path"] for op in ops), (
            f"single physical page must be the anchor: {[(o['action'], o['path']) for o in ops]}"
        )
        anchor = next(o for o in ops if o["action"] == "modify" and PAGE_REL in o["path"])
        assert "FilterPanel" in anchor.get("content", "")

    def test_zero_candidates_conflict(self, phase6_repo_copy):
        """0 physical page containers → blocking CONFLICT, plan must specify."""
        ws = shutil.copytree(phase6_repo_copy, f"{phase6_repo_copy}_z", dirs_exist_ok=True)
        os.remove(os.path.join(ws, PAGE_REL))
        try:
            result = _apply(ws)
            ex = result["execution"]
            assert ex["status"] == "clarification_needed", ex
            assert ex.get("conflict") == "anchor_ambiguity", ex
            assert ex.get("operations") == [], ex
            assert "0 physical anchors" in ex.get("detail", ""), ex
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_two_candidates_conflict(self, phase6_repo_copy):
        """2 physical page containers + no plan-specified anchor → CONFLICT."""
        ws = shutil.copytree(phase6_repo_copy, f"{phase6_repo_copy}_n", dirs_exist_ok=True)
        os.makedirs(os.path.dirname(os.path.join(ws, SECOND_PAGE_REL)), exist_ok=True)
        with open(os.path.join(ws, SECOND_PAGE_REL), "w") as f:
            f.write(SECOND_PAGE)
        try:
            result = _apply(ws)
            ex = result["execution"]
            assert ex["status"] == "clarification_needed", ex
            assert ex.get("conflict") == "anchor_ambiguity", ex
            assert "2 physical anchors" in ex.get("detail", ""), ex
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_forced_path_resolves_n_candidate_ambiguity(self, phase6_repo_copy):
        """Plan-specified anchor (forced) overrides N-candidate ambiguity."""
        ws = shutil.copytree(phase6_repo_copy, f"{phase6_repo_copy}_f", dirs_exist_ok=True)
        os.makedirs(os.path.dirname(os.path.join(ws, SECOND_PAGE_REL)), exist_ok=True)
        with open(os.path.join(ws, SECOND_PAGE_REL), "w") as f:
            f.write(SECOND_PAGE)
        forced = os.path.join(ws, SECOND_PAGE_REL)
        try:
            result = _apply(ws, forced_anchor_path=forced)
            ex = result["execution"]
            assert ex["status"] in ("ok", "verify_failed"), ex
            ops = ex["operations"]
            assert any(op["action"] == "modify" and SECOND_PAGE_REL in op["path"] for op in ops), (
                f"forced anchor must win over the N-candidate conflict: "
                f"{[(o['action'], o['path']) for o in ops]}"
            )
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_forced_never_reintroduces_semantic_scoring(self, phase6_repo_copy):
        """Forced path wins even when a 'better-looking' page exists — the
        decision is the plan's, not a score's."""
        ws = shutil.copytree(phase6_repo_copy, f"{phase6_repo_copy}_fs", dirs_exist_ok=True)
        os.makedirs(os.path.dirname(os.path.join(ws, SECOND_PAGE_REL)), exist_ok=True)
        with open(os.path.join(ws, SECOND_PAGE_REL), "w") as f:
            f.write(SECOND_PAGE)
        # Plan pins the LESS topical anchor (analytics page for a filter widget)
        forced = os.path.join(ws, SECOND_PAGE_REL)
        try:
            result = _apply(ws, forced_anchor_path=forced)
            ex = result["execution"]
            assert ex["status"] in ("ok", "verify_failed"), ex
            ops = ex["operations"]
            assert any(op["action"] == "modify" and SECOND_PAGE_REL in op["path"] for op in ops)
        finally:
            shutil.rmtree(ws, ignore_errors=True)