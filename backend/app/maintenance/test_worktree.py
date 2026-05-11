import os
import subprocess

REPO_ROOT = "/opt/agent-repos/agent-test-repo"
RUN_ID = "test-run"

WORKSPACE = f"/tmp/agent-runs/{RUN_ID}/workspace"

# 1. limpiar si existe
subprocess.run(
    ["git", "worktree", "remove", WORKSPACE, "--force"],
    cwd=REPO_ROOT,
    check=False
)

# 2. crear worktree limpio
subprocess.run(
    ["git", "worktree", "add", WORKSPACE, "-b", f"agent-{RUN_ID}"],
    cwd=REPO_ROOT,
    check=True
)

print("[TEST] worktree created")

# 3. escribir archivo directo (SIN tu engine)
file_path = os.path.join(WORKSPACE, "src/test.ts")
os.makedirs(os.path.dirname(file_path), exist_ok=True)

with open(file_path, "w") as f:
    f.write("console.log('hello test');")

print("[TEST] file written")

# 4. git status
status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=WORKSPACE,
    capture_output=True,
    text=True
).stdout

print("[TEST] git status:\n", status)

# 5. git diff
diff = subprocess.run(
    ["git", "diff", "--cached"],
    cwd=WORKSPACE,
    capture_output=True,
    text=True
).stdout

print("[TEST] git diff:\n", diff)