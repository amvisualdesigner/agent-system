import os
import json
import subprocess

from app.planner.execution_compiler import compile_plan
from app.executor.patch_executor import apply_operation
from app.policy.policy import validate_plan_policy, validate_operation
from app.utils.state import write_state
from app.executor.diff_generator import generate_diff
from app.utils.path_guard import guard_within
from app.config.settings import settings


def apply_engine(run_id, plan: dict, context, dry_run: bool = False):

    print(f"[apply] run_id = {run_id}")

    # ----------------------------
    # 1. RUNTIME SETUP
    # ----------------------------
    guard_within(context.workspace, settings.RUNS_DIR)
    guard_within(context.artifacts, settings.ARTIFACTS_DIR)
    os.makedirs(context.artifacts, exist_ok=True)

    print(f"[apply] artifacts = {context.artifacts}")

    # ----------------------------
    # 1.5 WORKTREE SETUP
    # ----------------------------
    print(f"[apply] workspace = {context.workspace}")

    # ----------------------------
    # 2. PLAN POLICY
    # ----------------------------
    ok, reason = validate_plan_policy(plan)

    print(f"[apply] validate_plan_policy = {ok}")

    if not ok:
        print(f"[apply] rejected policy: {reason}")

        return {
            "status": "rejected",
            "reason": reason
        }

    # ----------------------------
    # 3. COMPILE PLAN → OPERATIONS
    # ----------------------------
    operations = compile_plan(plan)

    print(f"[apply] operations = {len(operations)}")

    # ----------------------------
    # 4. EXECUTION POLICY + APPLY
    # ----------------------------
    results = []

    for i, op in enumerate(operations):

        print(f"[apply] op[{i}] = {op}")

        ok, reason = validate_operation(op)

        print(f"[apply] validate_operation = {ok}")

        if not ok:

            print(f"[apply] rejected operation: {reason}")

            return {
                "status": "rejected",
                "reason": reason,
                "run_id": run_id
            }

        result = apply_operation(op, context.workspace)

        print(f"[apply] result = {result}")

        results.append(result)

    # ----------------------------
    # 5. STATE
    # ----------------------------
    write_state(run_id, "apply")

    print("[apply] state written")

    # ----------------------------
    # 6. GENERATE DIFF
    # ----------------------------
    print("[apply] git add -A")

    subprocess.run(
        ["git", "add", "-A"],
        cwd=context.workspace,
        check=False
    )

    print("[apply] generating diff")

    diff = generate_diff(context.workspace)

    if not dry_run:
        result = subprocess.run(
            ["git", "commit", "-m", f"agent:{run_id}"],
            cwd=context.workspace,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            print("[apply][ERROR] git commit failed")
            print(result.stderr)

            return {
                "status": "rejected",
                "reason": "git_commit_failed",
                "error": result.stderr
            }

        print("[apply] committed")
    else:
        print("[apply] dry_run — skipped commit")

    print(f"[apply] diff length = {len(diff)}")

    if not diff:
        print("[apply] WARNING: diff is empty")

    # ----------------------------
    # 7. ARTIFACTS
    # ----------------------------
    print("[apply] writing artifacts")

    with open(f"{context.artifacts}/plan.json", "w") as f:
        json.dump(plan, f, indent=2)

    print("[apply] wrote plan.json")

    with open(f"{context.artifacts}/execution.json", "w") as f:
        json.dump({
            "operations": operations,
            "results": results
        }, f, indent=2)

    print("[apply] wrote execution.json")

    with open(f"{context.artifacts}/summary.json", "w") as f:
        json.dump({
            "run_id": run_id,
            "status": "ok",
            "files_created": [
                r.get("path") for r in results if r.get("status") == "created"
            ]
        }, f, indent=2)

    print("[apply] wrote summary.json")

    with open(f"{context.artifacts}/diff.patch", "w") as f:
        f.write(diff)

    print("[apply] wrote diff.patch")

    # ----------------------------
    # 8. RESPONSE
    # ----------------------------
    response = {
        "status": "ok" if diff else "rejected",
        "reason": None if diff else "missing_diff",
        "run_id": run_id,
        "dry_run": dry_run,
        "operations": operations,
        "execution": results,
        "workspace": context.workspace
    }

    print(f"[apply] response = {response}")

    return response