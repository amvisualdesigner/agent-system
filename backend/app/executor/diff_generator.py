import subprocess

import subprocess

def generate_diff(repo_root: str) -> str:
    print(f"[diff] repo_root = {repo_root}")

    # 1. estado repo
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True
    ).stdout

    print(f"[diff] git status:\n{status}")

    # 2. diff staged (CORRECTO)
    diff = subprocess.run(
        ["git", "diff", "--cached"],
        cwd=repo_root,
        capture_output=True,
        text=True
    ).stdout

    print(f"[diff] diff length = {len(diff)}")

    return diff