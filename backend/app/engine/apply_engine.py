import os
import json

from app.planner.execution_compiler import compile_plan
from app.executor.patch_executor import apply_operation
from app.policy.policy import validate_plan_policy, validate_operation
from app.utils.state import write_state


def apply_engine(plan: dict, context):
    # ----------------------------
    # 1. RUNTIME SETUP
    # ----------------------------
    run_id  = context["run_id"]
    workspace = context["workspace"]
    
    os.makedirs(workspace, exist_ok=True)
    os.makedirs(context["artifacts"], exist_ok=True)

    # ----------------------------
    # 2. PLAN POLICY
    # ----------------------------
    ok, reason = validate_plan_policy(plan)
    if not ok:
        return {
            "status": "rejected",
            "reason": reason
        }

    # ----------------------------
    # 3. COMPILE PLAN → OPERATIONS
    # ----------------------------
    operations = compile_plan(plan)

    # ----------------------------
    # 4. EXECUTION POLICY + APPLY
    # ----------------------------
    results = []

    for op in operations:

        ok, reason = validate_operation(op)
        if not ok:
            return {
                "status": "rejected",
                "reason": reason,
                "run_id": run_id
            }

        results.append(apply_operation(op, workspace))

    # ----------------------------
    # 5. STATE
    # ----------------------------
    write_state(run_id, "apply")

    # ----------------------------
    # 6. ARTIFACTS
    # ----------------------------
    with open(f"{context['artifacts']}/plan.json", "w") as f:
        json.dump(plan, f, indent=2)

    with open(f"{context['artifacts']}/execution.json", "w") as f:
        json.dump({
            "operations": operations,
            "results": results
        }, f, indent=2)
    
    with open(f"{context['artifacts']}/summary.json", "w") as f:
        json.dump({
            "run_id": run_id,
            "status": "ok",
            "files_created": [r["path"] for r in results if r.get("status") == "created"]
        }, f, indent=2)

    # ----------------------------
    # 7. RESPONSE
    # ----------------------------
    return {
        "status": "ok",
        "run_id": run_id,
        "workspace": workspace,
        "operations": operations,
        "execution": results
    }