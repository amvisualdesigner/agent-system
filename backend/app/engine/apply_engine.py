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
            if op.get("target") == "skill":
                print(f"[apply] skill validation failed — skipping: {reason}")
                results.append({
                    "status": "skipped",
                    "target": "skill",
                    "reason": f"fallback_to_file: {reason}",
                })
                continue
            print(f"[apply] rejected operation: {reason}")
            return {
                "status": "rejected",
                "reason": reason,
                "run_id": run_id
            }

        result = apply_operation(op, context.workspace)

        print(f"[apply] result = {result}")

        results.append(result)

    has_skills = any(r.get("target") == "skill" and r.get("status") == "executed" for r in results)
    has_files = any(r.get("target") == "file" for r in results)
    file_results = [r for r in results if r.get("target") == "file"]
    file_ok = all(r.get("status") != "error" for r in file_results)

    intent_fidelity = {
        "requested_skill": any(op.get("target") == "skill" for op in operations),
        "executed_skill": any(
            r.get("target") == "skill" and r.get("status") == "executed" for r in results
        ),
        "fallback_used": any(
            r.get("status") == "skipped"
            or str(r.get("reason", "")).startswith("fallback_to_file")
            for r in results
        ),
    }

    # skill-only runs skip git/diff — no filesystem changes
    if has_skills and not has_files:
        write_state(run_id, "apply")
        if intent_fidelity["requested_skill"] and not intent_fidelity["executed_skill"]:
            skill_status = "rejected"
            skill_reason = "skill_resolution_failed"
        else:
            skill_status = "ok"
            skill_reason = None
        response = {
            "status": skill_status,
            "reason": skill_reason,
            "run_id": run_id,
            "dry_run": dry_run,
            "operations": operations,
            "execution": results,
            "workspace": context.workspace,
            "execution_mode": "skill_only",
            "intent_fidelity": intent_fidelity,
        }
        with open(f"{context.artifacts}/execution.json", "w") as f:
            json.dump({"operations": operations, "results": results}, f, indent=2)
        with open(f"{context.artifacts}/summary.json", "w") as f:
            json.dump({
                "run_id": run_id,
                "status": skill_status,
                "skills_executed": [
                    r.get("name") for r in results if r.get("status") == "executed"
                ],
                "skill_asts": [
                    r.get("ast") for r in results if r.get("ast")
                ],
                "intent_fidelity": intent_fidelity,
            }, f, indent=2)
        return response

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
        ],
        "skills_executed": [
            r.get("name") for r in results if r.get("target") == "skill" and r.get("status") == "executed"
        ],
        "skill_asts": [
            r.get("ast") for r in results if r.get("target") == "skill" and r.get("ast")
        ]
    }, f, indent=2)

    print("[apply] wrote summary.json")

    with open(f"{context.artifacts}/diff.patch", "w") as f:
        f.write(diff)

    print("[apply] wrote diff.patch")

    # ----------------------------
    # 8. RESPONSE
    # ----------------------------
    if has_skills and not has_files:
        status = "ok"
        reason = None
    elif has_files:
        status = "ok" if (diff and file_ok) else "rejected"
        reason = None if (diff and file_ok) else ("missing_diff" if not diff else "file_execution_error")
    else:
        status = "ok" if diff else "rejected"
        reason = None if diff else "missing_diff"

    if intent_fidelity["requested_skill"] and not intent_fidelity["executed_skill"]:
        status = "rejected"
        reason = "skill_resolution_failed"

    execution_mode = (
        "skill_only" if has_skills and not has_files else
        "skill_mixed" if has_skills else
        "file_only"
    )

    response = {
        "status": status,
        "reason": reason,
        "run_id": run_id,
        "dry_run": dry_run,
        "operations": operations,
        "execution": results,
        "workspace": context.workspace,
        "execution_mode": execution_mode,
        "intent_fidelity": intent_fidelity,
    }

    print(f"[apply] response = {response}")

    return response