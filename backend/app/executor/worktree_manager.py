import os
import shutil
import subprocess

from app.config.settings import settings
from app.runtime.context import RunContext
from app.utils.run_id import validate_run_id
from app.utils.path_guard import guard_within


def ensure_worktree(context: RunContext) -> str:
    """
    Crea el worktree si no existe. Idempotente en 3 niveles:
      1. Instance flag (context.worktree_created)
      2. Filesystem check (workspace dir exists)
      3. create_worktree (solo si no existe en FS)
    Retorna workspace path.
    """
    if context.worktree_created:
        return context.workspace

    workspace = f"{settings.RUNS_DIR}/{context.run_id}"

    if os.path.isdir(workspace):
        context.workspace = workspace
        context.worktree_created = True
        print(f"[worktree] reuse existing at {workspace}")
        return workspace

    workspace = create_worktree(context.run_id)
    context.workspace = workspace
    context.worktree_created = True
    return workspace


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

    # ----------------------------
    # 7. copiar .opencode/ local gitignorado al worktree
    # ----------------------------
    src_opencode = os.path.join(repo_root, ".opencode")
    dst_opencode = os.path.join(workspace, ".opencode")
    if os.path.isdir(src_opencode):
        os.makedirs(dst_opencode, exist_ok=True)
        for fname in os.listdir(src_opencode):
            src = os.path.join(src_opencode, fname)
            dst = os.path.join(dst_opencode, fname)
            if os.path.isfile(src):
                if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                    try:
                        shutil.copy2(src, dst)
                        print(f"[worktree] seeded {fname}")
                    except Exception as e:
                        print(f"[worktree] WARN: failed to seed {fname}: {e}")

    print(f"[worktree] ready at {workspace}")

    return workspace