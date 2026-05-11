import os
import shutil
import subprocess

BASE_DIR = "/tmp/agent-runs"
REPO_ROOT = "/opt/agent-repos/agent-test-repo"


def cleanup():
    print("[cleanup] NUCLEAR START")

    # ----------------------------
    # 1. GIT WORKTREES
    # ----------------------------
    subprocess.run(
        ["git", "worktree", "prune", "--force"],
        cwd=REPO_ROOT,
        check=False
    )

    # eliminar branches agent-*
    try:
        branches = subprocess.check_output(
            ["git", "branch"],
            cwd=REPO_ROOT,
            text=True
        ).splitlines()
    except Exception:
        branches = []

    for b in branches:
        b = b.strip().replace("*", "").strip()

        if b.startswith("agent-"):
            print(f"[cleanup] deleting branch {b}")
            subprocess.run(
                ["git", "branch", "-D", b],
                cwd=REPO_ROOT,
                check=False
            )

    # ----------------------------
    # 2. FILESYSTEM TOTAL WIPE
    # ----------------------------
    if os.path.exists(BASE_DIR):
        for name in os.listdir(BASE_DIR):
            path = os.path.join(BASE_DIR, name)

            print(f"[cleanup] removing {path}")
            shutil.rmtree(path, ignore_errors=True)

    # ----------------------------
    # 3. FINAL CHECK (OPTIONAL)
    # ----------------------------
    subprocess.run(["git", "worktree", "prune"], cwd=REPO_ROOT)

    print("[cleanup] DONE")


if __name__ == "__main__":
    cleanup()