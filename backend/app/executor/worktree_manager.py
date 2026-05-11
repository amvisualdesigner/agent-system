import os
import shutil
import subprocess

from app.config.settings import settings


def create_worktree(run_id: str) -> str:
    """
    Crea un workspace aislado para el run usando git worktree.
    SIEMPRE desde HEAD (sin branches).
    """

    workspace = f"/tmp/agent-runs/{run_id}/workspace"
    repo_root = settings.REPO_ROOT

    print(f"[worktree] creating clean workspace for {run_id}")

    # ----------------------------
    # 1. limpiar estado git si existe
    # ----------------------------
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=repo_root,
        check=False
    )

    # ----------------------------
    # 2. eliminar workspace físico si existe
    # ----------------------------
    subprocess.run(
        ["rm", "-rf", workspace],
        check=False
    )

    if os.path.exists(workspace):
        shutil.rmtree(workspace, ignore_errors=True)

    # ----------------------------
    # 3. crear worktree limpio desde HEAD
    # ----------------------------
    subprocess.run(
        ["git", "worktree", "add", workspace, "HEAD"],
        cwd=repo_root,
        check=True
    )

    print(f"[worktree] ready at {workspace}")

    return workspace