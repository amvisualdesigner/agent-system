import os
import shutil
import subprocess

from app.config.settings import settings
from app.utils.run_id import validate_run_id
from app.utils.path_guard import guard_within


def create_worktree(run_id: str) -> str:
    """
    Crea un workspace aislado usando git worktree.

    ✔ Siempre desde base_branch (master)
    ✔ Rama única por run
    ✔ No contamina HEAD del repo base
    ✔ Mantiene contrato: retorna SOLO workspace (str)
    """

    validate_run_id(run_id)

    workspace = f"{settings.RUNS_DIR}/{run_id}"
    guard_within(workspace, settings.RUNS_DIR)
    repo_root = settings.REPO_ROOT
    branch = f"agent-{run_id[:8]}"
    base_branch = "master"

    print(f"[worktree] run_id={run_id}")
    print(f"[worktree] branch={branch} base={base_branch}")

    # ----------------------------
    # 1. eliminar worktree previo si existe
    # ----------------------------
    subprocess.run(
        ["git", "worktree", "remove", workspace, "--force"],
        cwd=repo_root,
        check=False
    )

    # ----------------------------
    # 2. limpiar worktrees huérfanos
    # ----------------------------
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=repo_root,
        check=False
    )

    # ----------------------------
    # 3. eliminar workspace físico si existe
    # ----------------------------
    subprocess.run(
        ["rm", "-rf", workspace],
        check=False
    )

    if os.path.exists(workspace):
        shutil.rmtree(workspace, ignore_errors=True)

    # ----------------------------
    # 4. asegurar base limpia
    # ----------------------------
    subprocess.run(
        ["git", "fetch", "--all"],
        cwd=repo_root,
        check=False
    )

    subprocess.run(
        ["git", "checkout", base_branch],
        cwd=repo_root,
        check=True
    )

    subprocess.run(
        ["git", "reset", "--hard", base_branch],
        cwd=repo_root,
        check=True
    )

    # ----------------------------
    # 5. crear worktree desde base explícita
    # ----------------------------
    subprocess.run(
        [
            "git",
            "worktree",
            "add",
            workspace,
            "-b",
            branch,
            base_branch
        ],
        cwd=repo_root,
        check=True
    )

    # ----------------------------
    # 6. identidad git en workspace
    # ----------------------------
    subprocess.run(
        ["git", "config", "user.email", "agent@local"],
        cwd=workspace,
        check=False
    )

    subprocess.run(
        ["git", "config", "user.name", "agent"],
        cwd=workspace,
        check=False
    )

    print(f"[worktree] ready at {workspace}")

    return workspace