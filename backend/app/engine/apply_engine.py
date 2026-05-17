import os
import json
import subprocess
import logging

from app.executor.patch_executor import apply_operation
from app.executor.patch_executor_dumb import apply_dumb as apply_dumb_op
from app.policy.policy import validate_plan_policy, validate_operation
from app.utils.state import write_state
from app.executor.diff_generator import generate_diff
from app.utils.path_guard import guard_within
from app.config.settings import settings
from app.config.feature_flags import FEATURE_FLAGS
from app.contracts.skill_ir import SkillIR
from app.contract_resolver.resolver import resolve as resolve_contract
from app.renderer.file_renderer import FileRenderer
from app.renderer.validators import validate_fileops

logger = logging.getLogger(__name__)


def _dump_execution_snapshot(run_id: str, plan: dict, operations: list, results: list):
    snapshot = {
        "run_id": run_id,
        "plan": plan,
        "operations": operations,
        "results": results,
    }
    path = f"{settings.ARTIFACTS_DIR}/{run_id}/baseline_execution.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)


def _run_git_flow(workspace: str, run_id: str, dry_run: bool) -> tuple[str | None, str | None]:
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=False)
    diff = generate_diff(workspace)
    if not dry_run:
        r = subprocess.run(
            ["git", "commit", "-m", f"agent:{run_id}"],
            cwd=workspace, capture_output=True, text=True,
        )
        if r.returncode != 0:
            return None, r.stderr
    return diff, None


def _write_artifacts(artifacts_dir: str, plan: dict, operations: list, results: list, diff: str):
    with open(f"{artifacts_dir}/plan.json", "w") as f:
        json.dump(plan, f, indent=2)
    with open(f"{artifacts_dir}/execution.json", "w") as f:
        json.dump({"operations": operations, "results": results}, f, indent=2)
    with open(f"{artifacts_dir}/summary.json", "w") as f:
        json.dump({
            "run_id": plan.get("run_id", ""),
            "status": "ok",
            "files_created": [r.get("path") for r in results if r.get("status") == "created"],
            "execution_mode": "renderer",
        }, f, indent=2)
    with open(f"{artifacts_dir}/diff.patch", "w") as f:
        f.write(diff)


def apply_engine(run_id, plan: dict, context, dry_run: bool = False):
    print(f"[apply] run_id = {run_id}")

    guard_within(context.workspace, settings.RUNS_DIR)
    guard_within(context.artifacts, settings.ARTIFACTS_DIR)
    os.makedirs(context.artifacts, exist_ok=True)

    # RENDERER PATH (primary)
    if FEATURE_FLAGS.get("renderer_active") and plan.get("skill_ir"):
        skill_ir = SkillIR.from_dict(plan["skill_ir"])
        result = resolve_contract(skill_ir)
        if not result.ok:
            return {"status": "rejected", "reason": result.reason}

        from app.contracts.skill_registry import get_contract
        contract = get_contract(skill_ir.contract_id, skill_ir.version)
        renderer = FileRenderer()
        fileops = renderer.render(result.ast, contract.renderer)

        ok, vreason = validate_fileops(fileops)
        if not ok:
            return {"status": "rejected", "reason": vreason}

        results = []
        for fop in fileops:
            result = apply_dumb_op(fop, context.workspace)
            results.append(result)

        logger.info("apply: contract_id=%s version=%d params=%s fileops_count=%d",
                    skill_ir.contract_id, skill_ir.version, skill_ir.params, len(fileops))

        diff, err = _run_git_flow(context.workspace, run_id, dry_run)
        if err:
            return {"status": "rejected", "reason": "git_commit_failed", "error": err}

        _write_artifacts(context.artifacts, plan, [fop.to_dict() for fop in fileops], results, diff or "")
        write_state(run_id, "apply")

        return {
            "status": "ok",
            "run_id": run_id,
            "dry_run": dry_run,
            "operations": [fop.to_dict() for fop in fileops],
            "execution": results,
            "workspace": context.workspace,
            "execution_mode": "renderer",
            "intent_fidelity": {"requested_skill": True, "executed_skill": True, "fallback_used": False},
        }

    # LEGACY PATH (frozen, transitional)
    ok, reason = validate_plan_policy(plan)
    if not ok:
        return {"status": "rejected", "reason": reason}

    operations = []
    for action in plan.get("actions", []):
        op = {
            "action": action.get("type", ""),
            "target": action.get("target", "file"),
            "path": action.get("file_path", ""),
            "name": action.get("name", ""),
            "params": action.get("params", {}),
            "diff": action.get("content", ""),
            "intent": action.get("intent", ""),
        }
        operations.append(op)

    results = []
    for i, op in enumerate(operations):
        ok, reason = validate_operation(op)
        if not ok:
            results.append({"status": "skipped", "reason": reason})
            continue
        result = apply_operation(op, context.workspace)
        results.append(result)

    diff, err = _run_git_flow(context.workspace, run_id, dry_run)
    if err:
        return {"status": "rejected", "reason": "git_commit_failed", "error": err}

    _dump_execution_snapshot(run_id, plan, operations, results)
    _write_artifacts(context.artifacts, plan, operations, results, diff or "")
    write_state(run_id, "apply")

    return {
        "status": "ok",
        "run_id": run_id,
        "dry_run": dry_run,
        "operations": operations,
        "execution": results,
        "workspace": context.workspace,
        "execution_mode": "legacy",
    }
