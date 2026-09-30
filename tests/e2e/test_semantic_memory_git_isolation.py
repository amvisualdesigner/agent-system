"""S2 Fase 1 — L1: aislar `semantic_memory.json` del commit funcional del Run.

Invariante bajo test:

> El commit de un Run representa EXCLUSIVAMENTE los cambios funcionales /
> materiales producidos por ese Run. El bookkeeping interno de Memory
> (`.opencode/semantic_memory.json`) conserva su policy exacta (load/merge/
> save/acumular en el workspace) pero NUNCA puede formar parte de un commit
> del Run, NUNCA produce un commit bookkeeping-only y NUNCA se filtra al
> commit de un Run posterior.

Escenarios obligatorios:
  1. Run funcional      → commit creado, contiene el cambio funcional, sin Memory.
  2. Run sin delta funcional (solo Memory) → NO_CHANGES, sin commit.
  3. Two runs consecutivos → commit A intacto; B (solo bookkeeping) sin commit.
  4. Run FAILED/conflict → sin commit; el residuo de Memory no se incorpora
     al siguiente commit.
  5. Session continuity → commits A y B funcional-aislados; sesión intacta.

Los tests usan apply_engine REAL (dry_run=False) contra un workspace-git real.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid

import pytest

from app.engine.apply_engine import FEATURE_FLAGS, apply_engine
from app.runtime.context import RunContext
from tests.e2e.helpers import build_plan_from_actions

MEM_DIR = ".opencode"
MEM_REL = os.path.join(MEM_DIR, "semantic_memory.json")
GITIGNORE_REL = os.path.join(MEM_DIR, ".gitignore")


@pytest.fixture(autouse=True)
def _no_verify(monkeypatch):
    """Keep the probe deterministic: verify_worktree (tsc/npm) is out of scope."""
    monkeypatch.setitem(FEATURE_FLAGS, "verify_worktree", False)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _git(args, cwd, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r


def _commit_count(ws) -> int:
    r = _git(["git", "rev-list", "--count", "HEAD"], cwd=ws)
    return int(r.stdout.strip())


def _head_files(ws) -> list[str]:
    """Files touched by HEAD commit."""
    r = _git(["git", "show", "--name-only", "--format=", "HEAD"], cwd=ws)
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _head_message(ws) -> str:
    return _git(["git", "log", "-1", "--pretty=%s"], cwd=ws).stdout.strip()


def _memory_size(ws) -> int:
    full = os.path.join(ws, MEM_REL)
    with open(full) as f:
        return len(json.load(f))


def _find_file(ws, basename) -> list[str]:
    out = []
    for root, _, files in os.walk(ws):
        if ".git" in root.split(os.sep):
            continue
        for f in files:
            if f == basename:
                out.append(os.path.relpath(os.path.join(root, f), ws))
    return out


def _apply(ws, artifacts, run_id, verb, contract_id="analytics.filter",
           target="presentation.filter_panel", params=None, attach_label=None):
    """Apply a deterministic plan for real (dry_run=False) on the workspace."""
    actions = [{"verb": verb, "target_capability": target, "confidence": 0.9}]
    if attach_label:
        actions[0]["attach"] = {
            "target": {"capability": "layout.page", "instance_label": attach_label},
        }
    plan = build_plan_from_actions(
        actions,
        params=params or {},
        contract_id=contract_id,
    )
    d = plan.to_dict()
    d["gate"] = {"blocked": False}
    ctx = RunContext(
        run_id=run_id, base_dir=ws, workspace=ws, artifacts=artifacts,
    )
    return apply_engine(run_id, d, ctx, dry_run=False)


def _ctx(run_id, ws, artifacts):
    return RunContext(run_id=run_id, base_dir=ws, workspace=ws, artifacts=artifacts)


# ---------------------------------------------------------------------------
# 1. Run funcional → commit funcional, sin Memory
# ---------------------------------------------------------------------------

class TestFunctionalRunCommit:

    def test_functional_run_commits_only_functional(self, create_workspace, artifacts_dir):
        ws = create_workspace
        run_id = str(uuid.uuid4())
        before = _commit_count(ws)

        res = _apply(ws, artifacts_dir, run_id, "create", attach_label="Page")
        assert res.get("execution", {}).get("status") == "ok", res

        assert _commit_count(ws) == before + 1, "funcional → exactamente 1 commit"
        assert _head_message(ws) == f"agent:{run_id}"
        head = _head_files(ws)

        # … y el commit contiene el cambio funcional (FilterPanel + montaje por attach).
        created = _find_file(ws, "FilterPanel.tsx")
        assert created, "Run A debe materializar FilterPanel.tsx"
        assert created[0] in head, "el cambio funcional debe estar en el commit"
        assert any("Page.tsx" in f for f in head), "el montaje de anclaje también es funcional"

        # Memory NO es contenido del commit.
        assert MEM_REL not in head, f"Memory no puede estar en el commit: {head}"
        # … pero sí conserva su policy: existe y acumula en el workspace.
        mem = os.path.join(ws, MEM_REL)
        assert os.path.exists(mem), "Memory sigue persistiendo en disco"


# ---------------------------------------------------------------------------
# 2. Run sin delta funcional (solo Memory) → NO_CHANGES, sin commit
# ---------------------------------------------------------------------------

class TestMemoryOnlyRun:

    def test_memory_only_run_is_no_changes(self, create_workspace, artifacts_dir):
        ws = create_workspace

        # Run A: cambio funcional real.
        run_a = str(uuid.uuid4())
        res_a = _apply(ws, artifacts_dir, run_a, "create", params={"filters": ["Status", "Region", "Date"]})
        assert res_a.get("execution", {}).get("status") == "ok", res_a
        after_a = _commit_count(ws)
        mem_before = _memory_size(ws)

        # Run B: MODIFY con otros params → mismo template genérico → sin delta
        # de archivo; el único cambio en disco es Memory (nuevo fingerprint).
        run_b = str(uuid.uuid4())
        res_b = _apply(ws, artifacts_dir, run_b, "modify", params={"filters": ["Date", "Status", "Region"]})
        assert res_b.get("execution", {}).get("status") == "no_changes", (
            f"Run solo-bookkeeping debe resultar en NO_CHANGES: {res_b}"
        )

        mem_after = _memory_size(ws)
        assert mem_after > mem_before, (
            "Run B debe perseguir un nuevo fingerprint (los cambios de params "
            "sí tocan Memory)"
        )

        # Sin commit bookkeeping-only.
        assert _commit_count(ws) == after_a, "no puede crearse un commit cuyo único contenido sea Memory"
        assert _head_message(ws) == f"agent:{run_a}", "el último commit sigue siendo el de Run A"


# ---------------------------------------------------------------------------
# 3. Two runs consecutivos — commit A intacto, B solo bookkeeping sin commit
# ---------------------------------------------------------------------------

class TestTwoConsecutiveRuns:

    def test_consecutive_runs_keep_commit_a_intact(self, create_workspace, artifacts_dir):
        ws = create_workspace

        run_a = str(uuid.uuid4())
        res_a = _apply(ws, artifacts_dir, run_a, "create", params={"filters": ["Status", "Region", "Date"]})
        assert res_a.get("execution", {}).get("status") == "ok", res_a
        commit_a = _head_message(ws)
        head_a = _head_files(ws)
        after_a = _commit_count(ws)
        assert MEM_REL not in head_a

        # Run B: solo bookkeeping.
        run_b = str(uuid.uuid4())
        res_b = _apply(ws, artifacts_dir, run_b, "modify", params={"filters": ["Date", "Status", "Region"]})
        assert res_b.get("execution", {}).get("status") == "no_changes", res_b

        # Commit A intacto.
        assert _head_message(ws) == commit_a == f"agent:{run_a}"
        assert _head_files(ws) == head_a, "el commit de A no puede verse alterado por B"
        # Run B no genera commit con semantic_memory.json.
        assert _commit_count(ws) == after_a, "B (bookkeeping-only) no puede crear commit"


# ---------------------------------------------------------------------------
# 4. Run FAILED/conflict → sin commit; Memory no deja residuo incorporable
# ---------------------------------------------------------------------------

class TestFailedRun:

    def test_failed_run_has_no_commit_and_no_residue_in_next(self, e2e_workspace, artifacts_dir):
        ws = e2e_workspace
        # Línea base commitada (el fixture solo inicializa git).
        _git(["git", "config", "user.email", "test@test.com"], cwd=ws)
        _git(["git", "config", "user.name", "Test"], cwd=ws)
        with open(os.path.join(ws, "seed.txt"), "w") as f:
            f.write("v0\n")
        _git(["git", "add", "-A"], cwd=ws)
        _git(["git", "commit", "-m", "seed"], cwd=ws)
        before = _commit_count(ws)

        # Run A: CREATE filter con attach a 'Page' que NO existe física →
        # target_not_found (conflicto bloqueante). Fallo sin commit.
        run_a = str(uuid.uuid4())
        res_a = _apply(ws, artifacts_dir, run_a, "create", params={"filters": ["Channel"]},
                       attach_label="Page")
        status_a = res_a.get("execution", {}).get("status")
        assert status_a in ("clarification_needed", "rejected"), res_a
        assert res_a.get("execution", {}).get("conflict") == "target_not_found", res_a
        assert _commit_count(ws) == before, "un Run fallido no puede crear commit"
        assert _head_message(ws) == "seed"

        # Memory igual persistió su evidencia (policy intacta)…
        mem_full = os.path.join(ws, MEM_REL)
        assert os.path.exists(mem_full), "Memory sigue escribiendo su evidencia"

        # … pero no deja NINGÚN residuo de bookkeeping stageable: el archivo de
        # memoria nunca es visible para git (el `.gitignore` ancla sí es visible
        # como untracked, es el bootstrap de una única vez que calibrará si/no).
        porcelain = _git(["git", "status", "--porcelain"], cwd=ws, check=False).stdout
        assert "semantic_memory.json" not in porcelain, (
            f"El residuo de Memory no puede ser visible para el siguiente Run: {porcelain!r}"
        )

        # Run B funcional posterior: commit con únicamente el cambio funcional.
        os.makedirs(os.path.join(ws, "src", "pages", "dashboard"), exist_ok=True)
        with open(os.path.join(ws, "src", "pages", "dashboard", "Page.tsx"), "w") as f:
            f.write("import React from 'react';\n"
                    "export function Page(){ return <div className='page'><h1>hi</h1></div>; }\n")
        _git(["git", "add", "-A"], cwd=ws)
        _git(["git", "commit", "-m", "baseline page"], cwd=ws)

        run_b = str(uuid.uuid4())
        res_b = _apply(ws, artifacts_dir, run_b, "create", params={"filters": ["Channel"]},
                       attach_label="Page")
        assert res_b.get("execution", {}).get("status") == "ok", res_b
        head_b = _head_files(ws)
        assert MEM_REL not in head_b, (
            "la memoria del Run fallido no puede incorporarse al commit siguiente"
        )
        created = _find_file(ws, "FilterPanel.tsx")
        assert created and created[0] in head_b
        assert _head_message(ws) == f"agent:{run_b}"


# ---------------------------------------------------------------------------
# 5. Session continuity — commits A y B aislados, sesión intacta
# ---------------------------------------------------------------------------

class TestSessionContinuity:

    def _session_sb(self, tmp_path, monkeypatch):
        from app.config.settings import settings
        from app.executor.session_manager import create_session
        from app.session.models import SessionStatus
        from app.state.session_state import load_session

        repo = tmp_path / "base_repo"
        repo.mkdir()
        _git(["git", "init", "-b", "master"], cwd=str(repo))
        _git(["git", "config", "user.email", "test@test.com"], cwd=str(repo))
        _git(["git", "config", "user.name", "Test"], cwd=str(repo))
        os.makedirs(os.path.join(str(repo), "src", "pages", "dashboard"), exist_ok=True)
        os.makedirs(os.path.join(str(repo), "src", "components"), exist_ok=True)
        with open(os.path.join(str(repo), "src", "pages", "dashboard", "Page.tsx"), "w") as f:
            f.write("import React from 'react';\n"
                    "export function Page(){ return <div className='page'><h1>hi</h1></div>; }\n")
        _git(["git", "add", "-A"], cwd=str(repo))
        _git(["git", "commit", "-m", "seed"], cwd=str(repo))

        monkeypatch.setattr(settings, "REPO_ROOT", str(repo))

        sid = str(uuid.uuid4())
        record = create_session(sid)
        return record, sid, SessionStatus, load_session

    def test_session_commits_isolated_and_session_intact(self, tmp_path, monkeypatch, artifacts_dir):
        record, sid, SessionStatus, load_session = self._session_sb(tmp_path, monkeypatch)
        ws = record.workspace
        assert record.session_id == sid

        # Run A: CREATE filter_panel → commit funcional A.
        run_a = str(uuid.uuid4())
        res_a = _apply(ws, artifacts_dir, run_a, "create", params={"filters": ["Status"]})
        assert res_a.get("execution", {}).get("status") == "ok", res_a
        msg_a = _head_message(ws)
        head_a = _head_files(ws)
        assert msg_a == f"agent:{run_a}"
        assert MEM_REL not in head_a
        fp_a = _find_file(ws, "FilterPanel.tsx")
        assert fp_a and fp_a[0] in head_a

        # Run B: CREATE timeseries → commit funcional B (contiene SOLO B + su
        # propios efectos funcionales; nunca Memory).
        run_b = str(uuid.uuid4())
        res_b = _apply(
            ws, artifacts_dir, run_b, "create",
            contract_id="dashboard.sales_overview",
            target="presentation.timeseries",
        )
        assert res_b.get("execution", {}).get("status") == "ok", res_b
        head_b = _head_files(ws)
        assert _head_message(ws) == f"agent:{run_b}"
        assert MEM_REL not in head_b
        ts = _find_file(ws, "Timeseries.tsx")
        assert ts and ts[0] in head_b

        # Continuidad física de la Session intacta.
        session = load_session(sid)
        assert session is not None
        assert session.status == SessionStatus.ACTIVE.value
        commits = _git(
            ["git", "log", "--pretty=%s", record.branch],
            cwd=str(record.workspace), check=False,
        ).stdout.splitlines()
        assert commits[0] == f"agent:{run_b}"
        assert commits[1] == f"agent:{run_a}"
        # workspace limpio: ni residuo ni bookkeeping pendiente.
        porcelain = _git(["git", "status", "--porcelain"], cwd=ws, check=False).stdout
        assert porcelain.strip() == "", f"el workspace debe quedar limpio: {porcelain!r}"