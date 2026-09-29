"""S1-B — Session lifecycle E2E (S2–S10 + full run + isolation).

Drives the REAL git mechanics of a Session (worktree/branch creation, the
single apply commit route via _run_git_flow, and the single session_merge
route) against a hermetic base repository. The semantic pipeline (interpret →
confirm → apply against an LLM) is not the subject here: git continuity,
commit-per-run, concurrency, recovery, merge clean/conflict and isolation are.

Session = physical continuity + lifecycle. Never a semantic authority.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import uuid

import pytest

from app.config.settings import settings
from app.engine.apply_engine import _run_git_flow
from app.executor.session_manager import (
    SessionPhysicalError,
    create_session,
    ensure_session_worktree,
    resolve_session,
)
from app.intent.models import RunPhase
from app.session.models import SessionStatus
from app.session.locks import keyed_run_scope, session_apply_lock
from app.state.run_state import save_run_state
from app.state.session_state import load_session, save_session


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------

def _git(args, cwd, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r


def _write(ws, rel, content):
    full = os.path.join(ws, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)


def _read(ws, rel):
    with open(os.path.join(ws, rel)) as f:
        return f.read()


def _count_commits_at(ws) -> int:
    r = _git(["git", "rev-list", "--count", "HEAD"], cwd=ws)
    return int(r.stdout.strip())


@pytest.fixture
def sb_repo(tmp_path, monkeypatch):
    """Hermetic base repo (branch master) wired as settings.REPO_ROOT."""
    repo = tmp_path / "base_repo"
    repo.mkdir()
    _git(["git", "init", "-b", "master"], cwd=str(repo))
    _git(["git", "config", "user.email", "test@test.com"], cwd=str(repo))
    _git(["git", "config", "user.name", "Test"], cwd=str(repo))
    _write(str(repo), "seed.txt", "v0\n")
    _git(["git", "add", "-A"], cwd=str(repo))
    _git(["git", "commit", "-m", "seed"], cwd=str(repo))

    monkeypatch.setattr(settings, "REPO_ROOT", str(repo))
    yield str(repo)

    _git(["git", "worktree", "prune", "--expire", "now"], cwd=str(repo), check=False)


@pytest.fixture
def new_session(sb_repo):
    """Fresh create_session + helpers to emulate one Run commit."""

    def _make(session_id: str | None = None) -> dict:
        sid = session_id or str(uuid.uuid4())
        record = create_session(sid)
        return dict(record=record, sid=sid)

    return _make


def _run_commit(record, run_id: str, files: dict) -> str:
    """Emulate one Run apply on the Session worktree via the single commit route."""
    ws = ensure_session_worktree(record)
    for rel, content in files.items():
        _write(ws, rel, content)
    diff, err, committed = _run_git_flow(ws, run_id, dry_run=False)
    assert err is None, f"run {run_id} commit failed: {err}"
    assert committed is True, f"run {run_id} expected a real change"
    save_run_state(run_id, {
        "phase": RunPhase.COMPLETED.value,
        "session_id": record.session_id,
    })
    current = load_session(record.session_id)
    if current is not None and run_id not in current.run_ids:
        save_session(record.session_id, run_ids=list(current.run_ids) + [run_id])
    return diff


def _git_branch_commits(record) -> list[str]:
    r = _git(
        ["git", "log", "--pretty=%s", record.branch],
        cwd=settings.REPO_ROOT, check=False,
    )
    return r.stdout.splitlines() if r.returncode == 0 else []


# ---------------------------------------------------------------------------
# S2 — worktree único / branch única
# ---------------------------------------------------------------------------

class TestS2UniqueWorktreeBranch:
    def test_one_session_one_worktree_one_branch(self, new_session):
        ent = new_session()
        record = ent["record"]
        assert record.status == SessionStatus.ACTIVE
        assert record.branch == f"agent/session-{record.session_id[:8]}"
        assert record.workspace.endswith(record.session_id)

        r = _git(["git", "worktree", "list", "--porcelain"], cwd=settings.REPO_ROOT)
        lines = r.stdout
        assert lines.count(f"worktree {record.workspace}") == 1
        assert f"branch refs/heads/{record.branch}" in lines

        # exactly one branch for the session convention
        branches = _git(["git", "for-each-ref", "--format=%(refname)", "refs/heads/agent/session-*"],
                        cwd=settings.REPO_ROOT).stdout.splitlines()
        assert branches == [f"refs/heads/{record.branch}"]

    def test_idempotent_reuse_same_record(self, new_session):
        ent = new_session()
        record = ent["record"]
        reused = create_session(record.session_id)
        assert reused.session_id == record.session_id
        assert reused.branch == record.branch
        assert reused.status == SessionStatus.ACTIVE
        # no duplicate branch
        branches = _git(["git", "for-each-ref", "--format=%(refname)", "refs/heads/"],
                        cwd=settings.REPO_ROOT).stdout.splitlines()
        assert branches.count(f"refs/heads/{record.branch}") == 1

    def test_no_branch_per_run(self, new_session):
        ent = new_session()
        record = ent["record"]
        run1 = str(uuid.uuid4())
        run2 = str(uuid.uuid4())
        _run_commit(record, run1, {"a.txt": "A\n"})
        _run_commit(record, run2, {"b.txt": "B\n"})
        branches = _git(["git", "for-each-ref", "--format=%(refname)", "refs/heads/agent/"],
                        cwd=settings.REPO_ROOT).stdout.splitlines()
        assert branches == [f"refs/heads/{record.branch}"], "no branch-per-run inside a Session"

    def test_physical_mismatch_marks_failed_without_deletion(self, new_session, sb_repo):
        ent = new_session()
        record = ent["record"]
        # sabotage: rename the session branch (record.branch no longer exists)
        other = f"agent/session-renamed-{record.session_id[:8]}"
        _git(["git", "branch", "-m", record.branch, other], cwd=sb_repo)

        with pytest.raises(SessionPhysicalError):
            create_session(record.session_id)

        reloaded = load_session(record.session_id)
        assert reloaded is not None and reloaded.status == SessionStatus.FAILED
        # never deletes the worktree
        assert os.path.isdir(record.workspace)

    def test_missing_worktree_explicit_error_no_silent_recreate(self, new_session):
        ent = new_session()
        record = ent["record"]
        shutil.rmtree(record.workspace, ignore_errors=True)

        with pytest.raises(SessionPhysicalError, match="never recreated silently"):
            ensure_session_worktree(record)
        with pytest.raises(SessionPhysicalError):
            create_session(record.session_id)
        assert load_session(record.session_id).status == SessionStatus.FAILED
        assert not os.path.isdir(record.workspace), "must never auto-recreate"


# ---------------------------------------------------------------------------
# S3 — continuidad
# ---------------------------------------------------------------------------

class TestS3Continuity:
    def test_run2_observes_run1_commit(self, new_session):
        ent = new_session()
        record = ent["record"]
        run1 = str(uuid.uuid4())
        run2 = str(uuid.uuid4())

        _run_commit(record, run1, {"feature.txt": "A\n"})
        ws = ensure_session_worktree(record)

        # Run 2 observes commit A before writing B
        log_before = _git(["git", "log", "--pretty=%s"], cwd=ws).stdout
        assert f"agent:{run1}" in log_before
        assert _read(ws, "feature.txt") == "A\n"

        _run_commit(record, run2, {"feature.txt": "A+B\n"})
        assert _read(ws, "feature.txt") == "A+B\n"
        commits = _git_branch_commits(record)
        assert f"agent:{run1}" in commits
        assert f"agent:{run2}" in commits


# ---------------------------------------------------------------------------
# S4 — aislamiento
# ---------------------------------------------------------------------------

class TestS4Isolation:
    def test_two_sessions_isolated(self, new_session):
        a = new_session()["record"]
        b = new_session()["record"]
        assert a.session_id != b.session_id
        assert a.workspace != b.workspace
        assert a.branch != b.branch

        _run_commit(a, str(uuid.uuid4()), {"secret.txt": "A-only\n"})

        ws_b = ensure_session_worktree(b)
        assert not os.path.exists(os.path.join(ws_b, "secret.txt")), "Session A leaks into B"
        b_commits = _git_branch_commits(b)
        assert not any("agent:" in c for c in b_commits), "Session A commits leak into B"

        ws_a = ensure_session_worktree(a)
        assert _read(ws_a, "secret.txt") == "A-only\n"


# ---------------------------------------------------------------------------
# S5 — commit por run (max 1) + NO_CHANGES
# ---------------------------------------------------------------------------

class TestS5OneCommitPerRun:
    def test_exactly_one_commit_per_run(self, new_session):
        ent = new_session()
        record = ent["record"]
        ws = ensure_session_worktree(record)
        before = _count_commits_at(ws)
        _run_commit(record, str(uuid.uuid4()), {"x.txt": "1\n"})
        after = _count_commits_at(ws)
        assert after == before + 1, "exactly one commit per successful Run"

    def test_no_changes_is_not_an_error(self, new_session):
        ent = new_session()
        record = ent["record"]
        ws = ensure_session_worktree(record)
        _run_commit(record, str(uuid.uuid4()), {"x.txt": "1\n"})
        before = _count_commits_at(ws)

        diff, err, committed = _run_git_flow(ws, str(uuid.uuid4()), dry_run=False)
        assert err is None, "NO_CHANGES must not be a git failure"
        assert committed is False
        assert diff is None
        assert _count_commits_at(ws) == before, "NO_CHANGES must not create an empty commit"


# ---------------------------------------------------------------------------
# S6 — concurrency: session lock
# ---------------------------------------------------------------------------

class TestS6ConcurrencyBlock:
    def test_lock_scope_keyed_by_session_then_run(self):
        sid = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        assert keyed_run_scope(sid, rid) == sid
        assert keyed_run_scope(None, rid) == rid

    def test_lock_is_not_reentrant_and_unique_per_scope(self):
        sid = str(uuid.uuid4())
        lk1 = session_apply_lock(sid)
        lk2 = session_apply_lock(sid)
        assert lk1 is lk2
        assert lk1.acquire(blocking=False) is True
        assert lk1.acquire(blocking=False) is False, "second apply must block"
        lk1.release()

    def test_merge_refused_while_lock_held_and_recovers(self, new_session, sb_repo):
        from app.api.session_routes import _merge_preconditions, session_merge

        ent = new_session()
        record = ent["record"]
        sid = record.session_id
        _run_commit(record, str(uuid.uuid4()), {"a.txt": "A\n"})

        lock = session_apply_lock(sid)
        lock.acquire(blocking=False)
        try:
            out = session_merge(sid)
            assert out["status"] == "conflict"
            assert out["reason"] == "session_busy"
            assert out["session_status"] == SessionStatus.CONFLICT.value
        finally:
            lock.release()

        # CONFLICT is recoverable: merge again once the lock is free
        record = resolve_session(sid)
        assert record.status == SessionStatus.CONFLICT
        assert _merge_preconditions(record) == []
        out2 = session_merge(sid)
        assert out2["status"] == "merged"
        assert load_session(sid).status == SessionStatus.MERGED


# ---------------------------------------------------------------------------
# S7 — recovery (no Memory)
# ---------------------------------------------------------------------------

class TestS7Recovery:
    def test_recover_from_session_id_after_module_restart(self, new_session):
        ent = new_session()
        record = ent["record"]
        sid = record.session_id
        run1 = str(uuid.uuid4())
        _run_commit(record, run1, {"persisted.txt": "A\n"})

        # snapshot of git truth BEFORE restart
        branch_commits_before = _git_branch_commits(record)

        # simulate process restart: reload modules (disk is the authority)
        ss = importlib.reload(__import__("app.state.session_state", fromlist=["load_session"]))
        sm = importlib.reload(__import__("app.executor.session_manager", fromlist=["resolve_session"]))

        recovered = sm.resolve_session(sid)
        assert recovered.session_id == sid
        assert recovered.branch == record.branch
        assert recovered.workspace == record.workspace
        assert recovered.status == SessionStatus.ACTIVE
        assert run1 in recovered.run_ids
        assert _git_branch_commits(record) == branch_commits_before, "git log is the continuity"

        ws = sm.ensure_session_worktree(recovered)
        assert os.path.isdir(ws)

        # a follow-up Run + merge still work, proving reconstruction without Memory
        _run_commit(recovered, str(uuid.uuid4()), {"persisted.txt": "A+B\n"})
        from app.api.session_routes import session_merge
        out = session_merge(sid)
        assert out["status"] == "merged"


# ---------------------------------------------------------------------------
# S8 — merge limpio
# ---------------------------------------------------------------------------

class TestS8MergeClean:
    def test_base_contains_a_plus_b_after_merge(self, new_session, sb_repo):
        ent = new_session()
        record = ent["record"]
        run1 = str(uuid.uuid4())
        run2 = str(uuid.uuid4())
        _run_commit(record, run1, {"feature.txt": "A\n"})
        _run_commit(record, run2, {"feature.txt": "A+B\n"})

        from app.api.session_routes import session_merge
        out = session_merge(record.session_id)
        assert out["status"] == "merged"
        assert out["merged_commit"]

        assert load_session(record.session_id).status == SessionStatus.MERGED
        # master contains both run commits AND the explicit session merge commit
        base_log = _git(["git", "log", "--pretty=%s"], cwd=sb_repo).stdout.splitlines()
        assert f"agent:{run1}" in base_log
        assert f"agent:{run2}" in base_log
        assert f"session:{record.session_id[:8]}" in base_log
        assert _read(sb_repo, "feature.txt") == "A+B\n"

    def test_merged_session_is_terminal(self, new_session, sb_repo):
        from fastapi import HTTPException

        from app.api.session_routes import session_merge
        ent = new_session()
        record = ent["record"]
        _run_commit(record, str(uuid.uuid4()), {"a.txt": "A\n"})
        assert session_merge(record.session_id)["status"] == "merged"
        # further merge refused on a terminal Session
        with pytest.raises(HTTPException):
            session_merge(record.session_id)
        assert load_session(record.session_id).status == SessionStatus.MERGED


# ---------------------------------------------------------------------------
# S9 — merge conflict: abort, base clean, Session CONFLICT
# ---------------------------------------------------------------------------

class TestS9MergeConflict:
    def test_conflict_aborts_and_keeps_base_clean(self, new_session, sb_repo):
        ent = new_session()
        record = ent["record"]
        sid = record.session_id
        _run_commit(record, str(uuid.uuid4()), {"conflict.txt": "A\n"})

        # base advances in parallel on the same file
        _write(sb_repo, "conflict.txt", "B\n")
        _git(["git", "add", "-A"], cwd=sb_repo)
        _git(["git", "commit", "-m", "base side"], cwd=sb_repo)

        from app.api.session_routes import session_merge
        out = session_merge(sid)
        assert out["status"] == "conflict"
        assert out["reason"] == "git_merge_conflict"
        assert load_session(sid).status == SessionStatus.CONFLICT

        # base restored after --abort, clean
        porcelain = _git(["git", "status", "--porcelain"], cwd=sb_repo).stdout
        assert porcelain == "", f"base must be clean after abort, got: {porcelain!r}"
        assert _read(sb_repo, "conflict.txt") == "B\n"

    def test_concurrent_apply_never_materializes_over_session(self, new_session, sb_repo):
        ent = new_session()
        record = ent["record"]
        sid = record.session_id
        _run_commit(record, str(uuid.uuid4()), {"c.txt": "A\n"})

        lock = session_apply_lock(sid)
        lock.acquire(blocking=False)
        try:
            from app.api.session_routes import session_merge
            out = session_merge(sid)
            assert out["status"] == "conflict"
        finally:
            lock.release()


# ---------------------------------------------------------------------------
# S10 — single merge route + agent_approve removal (source-level)
# ---------------------------------------------------------------------------

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backend")


class TestS10SingleMergeRoute:
    def test_single_productive_git_merge_callsite(self):
        productive = []
        aborts = []
        for root, _dirs, files in os.walk(BACKEND_DIR):
            if "__pycache__" in root:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        if '"git", "merge"' not in line and "'git', 'merge'" not in line:
                            continue
                        if '"git", "merge", "--no-ff"' in line:
                            productive.append((path, line.strip()))
                        elif '"git", "merge", "--abort"' in line:
                            aborts.append((path, line.strip()))
        assert len(productive) == 1, f"exactly one productive merge expected, got {productive}"
        assert "session_routes.py" in productive[0][0], productive
        assert len(aborts) == 1 and "session_routes.py" in aborts[0][0]

    def test_no_second_merge_mechanism(self):
        mcp = open(os.path.join(BACKEND_DIR, "mcp-server", "server.py"), encoding="utf-8").read()
        assert '"git", "merge"' not in mcp
        assert len(re.findall(r"session_merge", mcp)) >= 1


# ---------------------------------------------------------------------------
# API — HTTP surface of the Session lifecycle
# ---------------------------------------------------------------------------

class TestApiSessionSurface:
    """HTTP endpoints + session gates (interpret/confirm/apply)."""

    @staticmethod
    def _draft(run: str):
        from app.intent.models import InterpretationDraft
        return InterpretationDraft(
            interpretation_id=f"i-{run}",
            status="ok",
            contract_id="dashboard.sales_overview",
            contract_version=1,
            proposed_actions=[
                {"verb": "modify", "target_capability": "presentation.kpi_row",
                 "label": "KPI row", "confidence": 0.9}
            ],
            alternatives=[],
            params_proposed={},
            worktree_capabilities=[],
        )

    @pytest.fixture
    def client(self, sb_repo):
        from fastapi.testclient import TestClient

        import main as backend_main
        return TestClient(backend_main.app)

    def test_create_and_get_session_http(self, client, sb_repo):
        r = client.post("/session", json={})
        assert r.status_code == 200
        sid = r.json()["session_id"]
        g = client.get(f"/session/{sid}")
        assert g.status_code == 200
        body = g.json()
        assert body["session_id"] == sid
        assert body["status"] == SessionStatus.ACTIVE.value
        assert isinstance(body["merge_ready"], bool)
        assert isinstance(body["merge_reasons"], list)

    def test_interpret_persists_session_and_registers_run(self, client, sb_repo, new_session):
        import app.api.agent_interpret as mod_interpret
        ent = new_session()
        sid = ent["sid"]
        run = str(uuid.uuid4())

        mod_interpret.interpret = lambda message, conversation, index_snapshot: self._draft(run)
        r = client.post("/agent/interpret", json={
            "run_id": run,
            "message": "update the KPI metrics",
            "session_id": sid,
        })
        assert r.status_code == 200

        from app.state.run_state import load_run_state
        from app.state.session_state import load_session
        assert load_run_state(run).get("session_id") == sid
        assert run in load_session(sid).run_ids

    def test_interpret_rejects_merged_session(self, client, sb_repo, new_session):
        from app.api.session_routes import session_merge
        ent = new_session()
        record = ent["record"]
        sid = record.session_id
        _run_commit(record, str(uuid.uuid4()), {"a.txt": "A\n"})
        assert session_merge(sid)["status"] == "merged"

        r = client.post("/agent/interpret", json={
            "run_id": str(uuid.uuid4()),
            "message": "whatever",
            "session_id": sid,
        })
        assert r.status_code == 400

    def test_confirm_and_apply_rejected_on_inactive_session(self, client, sb_repo, new_session):
        from app.api.session_routes import session_merge
        ent = new_session()
        record = ent["record"]
        sid = record.session_id
        _run_commit(record, str(uuid.uuid4()), {"a.txt": "A\n"})
        assert session_merge(sid)["status"] == "merged"

        run = str(uuid.uuid4())
        save_run_state(run, {"phase": RunPhase.CONFIRMED.value, "session_id": sid})
        r = client.post("/agent/confirm", json={
            "run_id": run,
            "interpretation_id": "i-x",
            "contract_id": "x",
        })
        assert r.status_code == 200
        assert r.json().get("gate", {}).get("reason") == "session_not_active"

        run2 = str(uuid.uuid4())
        save_run_state(run2, {
            "phase": RunPhase.CONFIRMED.value,
            "session_id": sid,
            "confirmed_intent": {"actions": [{"verb": "create", "target_capability": "page"}]},
            "compiled_plan": {"intents": []},
            "gate": {"blocked": False},
        })
        r = client.post("/agent/apply", json={"run_id": run2, "dry_run": False})
        assert r.status_code == 400
        assert "Only ACTIVE sessions" in r.json()["detail"]


# ---------------------------------------------------------------------------
# E2E — full session run + isolation (the brief's mandatory flow)
# ---------------------------------------------------------------------------

class TestE2EFullSessionFlow:
    def test_create_run_run_commit_commit_merge_master_has_ab(self, new_session, sb_repo):
        ent = new_session()
        record = ent["record"]
        sid = record.session_id
        run1 = str(uuid.uuid4())
        run2 = str(uuid.uuid4())

        # Run 1 → commit A
        _run_commit(record, run1, {"module.txt": "A\n"})
        # Run 2 → observes A, commits B
        assert _read(ensure_session_worktree(record), "module.txt") == "A\n"
        _run_commit(record, run2, {"module.txt": "A+B\n"})

        from app.api.session_routes import session_merge
        out = session_merge(sid)
        assert out["status"] == "merged"
        assert _read(sb_repo, "module.txt") == "A+B\n"
        base_log = _git(["git", "log", "--pretty=%s"], cwd=sb_repo).stdout
        assert f"agent:{run1}" in base_log and f"agent:{run2}" in base_log
        assert f"session:{sid[:8]}" in base_log

    def test_session_a_never_sees_or_modifies_session_b(self, new_session):
        a = new_session()["record"]
        b = new_session()["record"]

        _run_commit(a, str(uuid.uuid4()), {"isolated.txt": "FROM-A\n"})
        ws_b = ensure_session_worktree(b)
        assert not os.path.exists(os.path.join(ws_b, "isolated.txt"))
        # B can still run and merge independently
        _run_commit(b, str(uuid.uuid4()), {"own.txt": "FROM-B\n"})
        # A's workspace untouched
        assert not os.path.exists(os.path.join(a.workspace, "own.txt"))