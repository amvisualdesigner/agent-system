#!/usr/bin/env python3
"""
Migrate existing runs from old nested structure to new flat structure.

Old (pre-fix):
  /opt/agent-repos/worktrees/{run_id}/workspace/  ← git worktree
  /opt/agent-repos/worktrees/{run_id}/artifacts/   ← plan.json, etc.

New (post-fix):
  /opt/agent-repos/worktrees/{run_id}/             ← git worktree (directly)
  /opt/agent-repos/artifacts/{run_id}/              ← plan.json, etc.

Also cleans up contaminated entries (non-UUID directories) from
previous path traversal tests.
"""

import os
import re
import shutil
import subprocess
import sys

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

RUNS_DIR = "/opt/agent-repos/worktrees"
ARTIFACTS_DIR = "/opt/agent-repos/artifacts"
REPO_ROOT = "/opt/agent-repos/agent-test-repo"


def log(msg: str):
    print(f"  {msg}")


def is_uuid(name: str) -> bool:
    return bool(UUID_RE.match(name))


def migrate_worktree(uuid: str):
    old_workspace = os.path.join(RUNS_DIR, uuid, "workspace")
    new_workspace = os.path.join(RUNS_DIR, uuid)
    branch = f"agent-{uuid[:8]}"

    if not os.path.isdir(os.path.join(old_workspace, ".git")):
        log(f"  no git worktree at old path, skipping")
        return False

    log(f"  migrating git worktree: {old_workspace} -> {new_workspace}")

    subprocess.run(
        ["git", "worktree", "remove", old_workspace, "--force"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )

    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )

    result = subprocess.run(
        ["git", "worktree", "add", new_workspace, branch],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        log(f"  WARN: worktree add failed: {result.stderr.strip()}")
        return False

    log(f"  worktree migrated successfully")
    return True


def migrate_artifacts(uuid: str):
    old_artifacts = os.path.join(RUNS_DIR, uuid, "artifacts")
    new_artifacts = os.path.join(ARTIFACTS_DIR, uuid)

    if not os.path.isdir(old_artifacts):
        return

    files = os.listdir(old_artifacts)
    if not files:
        log(f"  no artifacts to migrate")
        return

    os.makedirs(new_artifacts, exist_ok=True)

    for fname in files:
        src = os.path.join(old_artifacts, fname)
        dst = os.path.join(new_artifacts, fname)
        shutil.move(src, dst)
        log(f"  moved: {src} -> {dst}")

    try:
        os.rmdir(old_artifacts)
        log(f"  removed old artifacts dir: {old_artifacts}")
    except OSError:
        pass


def remove_old_workspace_subdir(uuid: str):
    old_workspace = os.path.join(RUNS_DIR, uuid, "workspace")
    if os.path.isdir(old_workspace):
        shutil.rmtree(old_workspace, ignore_errors=True)
        log(f"  removed old workspace subdir: {old_workspace}")


def clean_contaminated(name: str):
    path = os.path.join(RUNS_DIR, name)
    log(f"  removing contaminated: {path}")
    shutil.rmtree(path, ignore_errors=True)


def clean_tmp_paths():
    for item in ["/tmp/agent-runs"]:
        if os.path.exists(item):
            log(f"  removing tmp fallback: {item}")
            shutil.rmtree(item, ignore_errors=True)


def main():
    print("=== Migration: worktree structure ===")
    print()

    contaminated = []
    uuids = []

    for name in os.listdir(RUNS_DIR):
        entry = os.path.join(RUNS_DIR, name)
        if not os.path.isdir(entry):
            continue
        if name == "state.json":
            continue
        if is_uuid(name):
            uuids.append(name)
        else:
            contaminated.append(name)

    # Step 1: clean contamination
    if contaminated:
        print("--- Cleaning contaminated entries ---")
        for name in sorted(contaminated):
            clean_contaminated(name)
        print()
    else:
        print("(no contaminated entries found)")
        print()

    # Step 2: migrate worktrees + artifacts for each UUID
    print("--- Migrating valid UUID runs ---")
    for uuid in sorted(uuids):
        print(f"[{uuid}]")

        has_old_workspace = os.path.isdir(os.path.join(RUNS_DIR, uuid, "workspace", ".git"))
        has_new_workspace = os.path.isdir(os.path.join(RUNS_DIR, uuid, ".git"))

        if has_old_workspace and not has_new_workspace:
            migrate_worktree(uuid)
        elif has_new_workspace:
            log(f"  worktree already at new path, skipping")
        else:
            log(f"  no git worktree found")

        old_artifacts_dir = os.path.join(RUNS_DIR, uuid, "artifacts")
        if os.path.isdir(old_artifacts_dir):
            migrate_artifacts(uuid)

        remove_old_workspace_subdir(uuid)

        new_workspace_git = os.path.isdir(os.path.join(RUNS_DIR, uuid, ".git"))
        log(f"  post-migration: workspace_has_git={new_workspace_git}")
        print()

    # Step 3: clean /tmp fallback paths
    print("--- Cleaning /tmp fallback paths ---")
    clean_tmp_paths()
    print()

    print("=== Migration complete ===")


if __name__ == "__main__":
    main()
