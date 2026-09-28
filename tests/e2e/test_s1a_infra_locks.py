"""S1-A — infrastructure lifecycle locks.

Enforces the S1-A contract before Session Lifecycle:

  1. There is exactly ONE productive git-commit path (_run_git_flow), used only
     by a successful Apply. Empty applies yield the explicit NO_CHANGES result
     — never an empty commit and never a spurious git failure.
  2. RunPhase.COMPLETED is only reachable from a real successful apply
     (accepted + verified + committed, or explicit NO_CHANGES). Every other
     outcome (rejected / verify_failed / clarification_needed / error) is
     terminal FAILED, and the apply outcome is persisted in run_state.
  3. verify_failed stages the worktree and STOPS: no commit, and no destructive
     reset/clean/checkout of the worktree state.
  4. The legacy approve→commit→merge path is gone: no /runs/{id}/approve route,
     no approve_run/get_base_branch, and the MCP agent_approve no longer calls
     it (marked pending session_merge migration). No /maintenance/cleanup HTTP
     and no /agent/latest either.
  5. agent_run is interpret-only: it can never produce confirmed/applying/
     completed on its own (no auto-confirm, no confirm/apply delegation).
"""

import inspect
import os
import shutil
import subprocess
import tempfile
import uuid

import pytest

from app.utils.run_id import validate_run_id


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _git_repo(prefix: str) -> str:
    """Fresh git repo (settings.RUNS_DIR parent) with identity configured."""
    from app.config.settings import settings
    tmpdir = tempfile.mkdtemp(prefix=prefix, dir=settings.RUNS_DIR)
    subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)
    return tmpdir


def _commit_all(workspace: str, msg: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=workspace, capture_output=True)


def _count_commits(workspace: str) -> int:
    r = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=workspace, capture_output=True, text=True,
    )
    return int(r.stdout.strip())


def _write(workspace: str, rel: str, content: str) -> None:
    full = os.path.join(workspace, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)


@pytest.fixture
def run_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# S1-A.1 — single commit route + NO_CHANGES
# ---------------------------------------------------------------------------

class TestSingleCommitRoute:
    """_run_git_flow is the ONLY git-commit path in the backend."""

    def test_only_one_commit_callsite_in_backend(self):
        backend_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "backend"
        )
        matches = []
        for root, _dirs, files in os.walk(backend_dir):
            if "__pycache__" in root:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        if '"git", "commit"' in line or "'git', 'commit'" in line:
                            matches.append((path, line.strip()))
        # Exactly one site may invoke `git commit`.
        assert len(matches) == 1, f"Expected exactly one git-commit site, got {matches}"
        assert matches[0][0].endswith("backend/app/engine/apply_engine.py"), matches

    def test_zero_merge_callsites_in_backend(self):
        backend_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "backend"
        )
        for root, _dirs, files in os.walk(backend_dir):
            if "__pycache__" in root:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        assert '"git", "merge"' not in line and "'git', 'merge'" not in line, (
                            f"{path}: git merge calls must not exist after S1-A.4"
                        )

    def test_real_changes_produce_exactly_one_commit(self, run_id):
        from app.engine.apply_engine import _run_git_flow
        ws = _git_repo("s1a_commit_")
        try:
            _write(ws, "a.txt", "v1")
            _commit_all(ws, "seed")
            before = _count_commits(ws)

            _write(ws, "a.txt", "v2")
            diff, err, committed = _run_git_flow(ws, run_id, dry_run=False)

            assert err is None
            assert committed is True
            assert diff and "a.txt" in diff
            after = _count_commits(ws)
            assert after == before + 1, "exactly one commit per successful apply"

            head_msg = subprocess.run(
                ["git", "log", "-1", "--pretty=%B"], cwd=ws,
                capture_output=True, text=True,
            ).stdout.strip()
            assert head_msg == f"agent:{run_id}"
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_no_changes_reports_no_changes_without_commit(self, run_id):
        from app.engine.apply_engine import _run_git_flow
        ws = _git_repo("s1a_nochange_")
        try:
            _write(ws, "a.txt", "v1")
            _commit_all(ws, "seed")
            before = _count_commits(ws)

            diff, err, committed = _run_git_flow(ws, run_id, dry_run=False)

            assert err is None, (
                "an empty apply must be NO_CHANGES, not a spurious git failure"
            )
            assert committed is False
            assert diff is None
            assert _count_commits(ws) == before, "NO_CHANGES must not create an empty commit"
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_dry_run_never_commits(self, run_id):
        from app.engine.apply_engine import _run_git_flow
        ws = _git_repo("s1a_dry_")
        try:
            _write(ws, "a.txt", "v1")
            _commit_all(ws, "seed")
            before = _count_commits(ws)

            _write(ws, "a.txt", "v2")
            _run_git_flow(ws, run_id, dry_run=True)

            assert _count_commits(ws) == before, "dry_run must not commit"
        finally:
            shutil.rmtree(ws, ignore_errors=True)


# ---------------------------------------------------------------------------
# S1-A.2 / S1-A.3 — phase mapping + persisted apply_result
# ---------------------------------------------------------------------------

class TestPhaseMapping:
    """_agent_apply maps the engine result onto the RunPhase machine."""

    def _run_apply(self, run_id, engine_result, monkeypatch):
        from app.api.agent_apply import _agent_apply
        from app.contracts.apply_request import ApplyRequest
        import app.api.agent_apply as mod
        from app.state.run_state import save_run_state
        from app.intent.models import RunPhase

        save_run_state(run_id, {
            "phase": RunPhase.CONFIRMED.value,
            "confirmed_intent": {"actions": [{"verb": "create", "target_capability": "page"}]},
            "compiled_plan": {"intents": []},
            "gate": {"blocked": False},
        })

        fake_ctx = type("Ctx", (), {"workspace": "/tmp/irrelevant"})()
        monkeypatch.setattr(mod, "build_context", lambda run_id: fake_ctx)
        monkeypatch.setattr(mod, "ensure_worktree", lambda ctx: ctx.workspace)
        monkeypatch.setattr(mod, "apply_engine", lambda *a, **k: engine_result)

        return _agent_apply(ApplyRequest(run_id=run_id, dry_run=False))

    @pytest.mark.parametrize("status", ["ok", "no_changes"])
    def test_success_statuses_complete(self, run_id, status, monkeypatch):
        from app.state.run_state import load_run_state
        from app.intent.models import RunPhase
        result = self._run_apply(run_id, {"execution": {"status": status}}, monkeypatch)
        assert result["execution"]["status"] == status
        state = load_run_state(run_id)
        assert state["phase"] == RunPhase.COMPLETED.value
        assert state["apply_result"] == status

    @pytest.mark.parametrize("status", ["rejected", "verify_failed", "clarification_needed", "error"])
    def test_non_success_statuses_fail(self, run_id, status, monkeypatch):
        from app.state.run_state import load_run_state
        from app.intent.models import RunPhase
        result = self._run_apply(
            run_id,
            {"execution": {"status": status, "reason": "some_reason"}},
            monkeypatch,
        )
        assert result["execution"]["status"] == status
        state = load_run_state(run_id)
        assert state["phase"] == RunPhase.FAILED.value, (
            f"status={status} must never reach COMPLETED"
        )
        assert state["apply_result"] == status

    def test_verify_failed_never_completes(self, run_id, monkeypatch):
        from app.state.run_state import load_run_state
        from app.intent.models import RunPhase
        self._run_apply(
            run_id,
            {"execution": {"status": "verify_failed", "diff": "staged-diff"}},
            monkeypatch,
        )
        assert load_run_state(run_id)["phase"] == RunPhase.FAILED.value


class TestVerifyFailedNoDestructiveReset:
    """S1-A.3: verify_failed stages and STOPS — never resets/cleans the worktree."""

    APPLY_ENGINE_PATH = os.path.join(
        os.path.dirname(__file__), "..", "..", "backend", "app", "engine", "apply_engine.py"
    )

    def _git_section(self) -> str:
        src = open(self.APPLY_ENGINE_PATH, encoding="utf-8").read()
        start = src.index("verify_failed")
        return src[start:]

    def test_no_reset_clean_checkout_in_verify_path(self):
        section = self._git_section()
        for forbidden in (
            '"git", "reset"', "'git', 'reset'",
            '"git", "clean"', "'git', 'clean'",
            '"git", "checkout"', "'git', 'checkout'",
        ):
            assert forbidden not in section, (
                "verify_failed must preserve the worktree state as-is "
                f"(found {forbidden!r} in the verify/git flow)."
            )

    def test_verify_failed_stages_diff_before_stopping(self):
        section = self._git_section()
        assert '"git", "add", "-A"' in section or "'git', 'add', '-A'" in section


# ---------------------------------------------------------------------------
# S1-A.4 / S1-A.5 / S1-A.6 — legacy surface removed
# ---------------------------------------------------------------------------

BACKEND_SRC = os.path.join(
    os.path.dirname(__file__), "..", "..", "backend"
)
MCP_SRC = os.path.join(BACKEND_SRC, "mcp-server", "server.py")


class TestApprovalLegacyRemoved:
    """No approve Run → commit → merge path survives."""

    def test_approve_route_not_registered(self):
        import main as backend_main
        paths = {r.path for r in backend_main.app.routes}
        assert "/runs/{run_id}/approve" not in paths
        assert "/maintenance/cleanup" not in paths
        assert "/agent/latest" not in paths

    def test_approve_implementation_gone_from_main(self):
        from pathlib import Path
        src = Path(os.path.join(BACKEND_SRC, "main.py")).read_text()
        for token in ("approve_run", "get_base_branch", "/approve", "maintenance/cleanup", "agent/latest"):
            assert token not in src, f"legacy token {token!r} must be removed"

    def test_mcp_approve_no_longer_calls_approve(self):
        src = open(MCP_SRC, encoding="utf-8").read()
        assert "/runs/" in src and "/approve" not in src, (
            "agent_approve must not POST to /runs/{id}/approve"
        )
        assert "pending_session_merge_migration" in src
        for token in ('"git", "commit"', '"git", "merge"', '"git", "branch"'):
            assert token not in src, f"MCP must never run git ops (found {token!r})"

    def test_mcp_approve_tool_kept_but_inert(self):
        src = open(MCP_SRC, encoding="utf-8").read()
        assert "async def agent_approve" in src, (
            "agent_approve is kept (not removed) but marked pending migration"
        )


class TestAgentRunNoAutoConfirm:
    """agent_run must never reach CONFIRMED/APPLYING/COMPLETED on its own."""

    def test_agent_run_is_interpret_only(self):
        src = open(MCP_SRC, encoding="utf-8").read()
        run_src = src[src.index("async def agent_run"):src.index("# -------------------------", src.index("async def agent_run"))]
        assert '"/agent/confirm"' not in run_src, "agent_run must not call confirm"
        assert '"/agent/apply"' not in run_src, "agent_run must not call apply"
        assert '"status": "awaiting_confirmation"' in run_src
        for token in ("auto-accept", "No human-in-the-loop", "auto-confirm"):
            assert token not in run_src, f"auto-confirm removed in agent_run (found {token!r})"

    def test_agent_reject_removed(self):
        src = open(MCP_SRC, encoding="utf-8").read()
        assert "agent_reject" not in src, "agent_reject (dead /reject) must be removed"
        assert "/reject" not in src


class TestCleanupHttpRemoved:
    def test_no_destructive_http_cleanup(self):
        import main as backend_main
        paths = {r.path for r in backend_main.app.routes}
        assert "/maintenance/cleanup" not in paths
        assert not any("cleanup" in p for p in paths)


# ---------------------------------------------------------------------------
# run_id contract sanity (backend side mirrors orchestrator tests)
# ---------------------------------------------------------------------------

class TestRunIdContract:
    def test_uuid_is_v4_format(self):
        run_id = str(uuid.uuid4())
        assert validate_run_id(run_id) == run_id

    def test_invalid_rejected(self):
        with pytest.raises(ValueError, match="Invalid run_id"):
            validate_run_id("not-a-uuid")