import os
import shutil
import subprocess

BASE_DIR = "/tmp/agent-runs"
REPO_ROOT = "/opt/agent-repos/agent-test-repo"

# usuario dueño del repo git 
GIT_USER = "agentsys"


def run_git(cmd):
    """
    Ejecuta git como el usuario propietario del repo.
    Evita problemas de permisos en .git/objects
    """
    full_cmd = ["sudo", "-u", GIT_USER] + cmd

    result = subprocess.run(
        full_cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True
    )

    print("[git]", " ".join(cmd))
    if result.stdout:
        print("[git stdout]", result.stdout.strip())
    if result.stderr:
        print("[git stderr]", result.stderr.strip())

    return result


def cleanup():
    print("[cleanup] NUCLEAR START")

    # ----------------------------
    # 1. GIT WORKTREES (como Y)
    # ----------------------------
    run_git(["git", "worktree", "prune", "--force"])

    # ----------------------------
    # 2. BRANCHES agent-* (como Y)
    # ----------------------------
    result = run_git(["git", "branch"])

    if result.returncode == 0:
        branches = result.stdout.splitlines()

        for b in branches:
            b = b.strip().replace("*", "").strip()

            if b.startswith("agent-"):
                print(f"[cleanup] deleting branch {b}")

                run_git(["git", "branch", "-D", b])

    # ----------------------------
    # 3. FILESYSTEM /tmp (como X)
    # ----------------------------
    if os.path.exists(BASE_DIR):
        for name in os.listdir(BASE_DIR):
            path = os.path.join(BASE_DIR, name)

            print(f"[cleanup] removing {path}")
            shutil.rmtree(path, ignore_errors=True)

    # ----------------------------
    # 4. FINAL PRUNE (como Y)
    # ----------------------------
    run_git(["git", "worktree", "prune"])

    print("[cleanup] DONE")


if __name__ == "__main__":
    cleanup()