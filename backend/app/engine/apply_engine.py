"""Apply engine — GraphIR-only pipeline.

Executes a plan against a workspace using the GraphIR pipeline:
  SkillIR → IntentPlan → GraphIRBuilder → GraphIR
  → LayoutDerivationEngine → GraphIRLayout
  → BackendRenderer → list[FileOp]

No legacy AST path. No slot system. No feature flags.
"""
import os
import json
import subprocess
import logging

from app.executor.patch_executor_dumb import apply_dumb as apply_dumb_op
from app.policy.policy import validate_plan_policy, validate_operation
from app.utils.state import write_state
from app.executor.diff_generator import generate_diff
from app.utils.path_guard import guard_within
from app.config.settings import settings
from app.contracts.skill_ir import SkillIR
from app.contracts.skill_registry import get_contract
from app.graphir.intent import IntentPlan, IntentNode, IntentExtensionRegistry
from app.graphir.builder import GraphIRBuilder
from app.graphir.pipeline import GraphIRPipeline
from app.graphir.validator import GraphIRValidator
from app.graphir.backends import ReactBackend, BackendConfig
from app.graphir.utils import validate_fileops
from app.graphir.utils import extract_component_name

logger = logging.getLogger(__name__)


def _skill_ir_to_intent_plan(skill_ir: SkillIR) -> IntentPlan:
    """Convert a SkillIR into an IntentPlan.

    Temporary adapter until the LLM produces IntentPlan directly.
    Resolves the contract, validates params, and creates IntentNodes
    from the contract's slot definitions.
    """
    contract = get_contract(skill_ir.contract_id, skill_ir.version)
    if contract is None:
        raise ValueError(f"Contract not found: {skill_ir.contract_id}@{skill_ir.version}")

    schema = contract.input_schema
    params = dict(skill_ir.params)

    required = schema.get("required", [])
    properties = schema.get("properties", {})
    for field in required:
        if field not in params:
            raise ValueError(f"missing required field: {field}")

    for field in params:
        if field not in properties:
            raise ValueError(f"unknown field: {field}")
        prop = properties[field]
        if "enum" in prop and params[field] not in prop["enum"]:
            raise ValueError(
                f"field '{field}': '{params[field]}' not in {prop['enum']}"
            )

    for field, prop in properties.items():
        if field not in params and "default" in prop:
            params[field] = prop["default"]

    resolved_params = params

    slots = contract.ast_template.get("slots", [])
    intents: list[IntentNode] = []

    file_count = len(contract.renderer.get("files", []))
    if file_count > 0:
        intents.append(IntentNode(type="PAGE", params={}))

    for slot in slots:
        intent_type = _resolve_intent_type(slot.get("type", ""))
        props = _resolve_props(slot.get("props", {}), resolved_params)
        intents.append(IntentNode(type=intent_type, params=props))

    return IntentPlan(
        contract_id=skill_ir.contract_id,
        version=skill_ir.version,
        confidence=skill_ir.confidence,
        intents=intents,
        params=resolved_params,
    )


def _resolve_intent_type(graphir_type: str) -> str:
    """Map GraphIR type name back to IntentType name."""
    from app.graphir.intent import IntentType, _INTENT_TO_GRAPHIR_TYPE
    reverse = {v: k.name for k, v in _INTENT_TO_GRAPHIR_TYPE.items()}
    return reverse.get(graphir_type, graphir_type)


def _resolve_props(props_template: dict, params: dict) -> dict:
    resolved = {}
    for prop_name, param_key in props_template.items():
        if param_key in params:
            resolved[prop_name] = params[param_key]
    return resolved


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
            "execution_mode": "graphir",
        }, f, indent=2)
    with open(f"{artifacts_dir}/diff.patch", "w") as f:
        f.write(diff)


def apply_engine(run_id, plan: dict, context, dry_run: bool = False, compiler_mode: str = "strict"):
    """Execute a plan against a workspace using the GraphIR pipeline.

    Pipeline:
      1. Convert SkillIR → IntentPlan (temporary adapter)
      2. GraphIRPipeline: IntentPlan → (GraphIR, GraphIRLayout)
      3. BackendRenderer: (GraphIR, GraphIRLayout, config) → FileOps
      4. validate_fileops → apply → git commit
    """
    guard_within(context.workspace, settings.RUNS_DIR)
    guard_within(context.artifacts, settings.ARTIFACTS_DIR)
    os.makedirs(context.artifacts, exist_ok=True)

    skill_ir = plan.get("skill_ir")
    if not skill_ir:
        return {"status": "rejected", "reason": "no_skill_ir"}

    skill_ir_obj = SkillIR.from_dict(skill_ir)
    contract = get_contract(skill_ir_obj.contract_id, skill_ir_obj.version)
    if contract is None:
        return {"status": "rejected", "reason": f"contract_not_found:{skill_ir_obj.contract_id}"}

    # Step 1: SkillIR → IntentPlan
    try:
        intent_plan = _skill_ir_to_intent_plan(skill_ir_obj)
    except ValueError as e:
        return {"status": "rejected", "reason": str(e)}

    # Step 2: GraphIR pipeline (build + layout + validate)
    try:
        graph, graph_layout = GraphIRPipeline.run(intent_plan)
    except ValueError as e:
        return {"status": "rejected", "reason": f"graphir:{e}"}

    # Step 3: BackendRenderer (ReactBackend)
    files = contract.renderer.get("files", [])
    base_path = contract.renderer.get("base_path", "")
    path_map = {}
    for f in files:
        comp = extract_component_name(f["path"])
        if comp:
            path_map[comp] = f["path"]
    backend_config = BackendConfig(
        output_base_path=base_path,
        path_map=path_map,
    )
    backend = ReactBackend()
    fileops = backend.render(graph, graph_layout, backend_config)

    ok, vreason = validate_fileops(fileops)
    if not ok:
        return {"status": "rejected", "reason": vreason}

    results = []
    for fop in fileops:
        result = apply_dumb_op(fop, context.workspace)
        results.append(result)

    logger.info(
        "apply: contract_id=%s version=%d params=%s fileops_count=%d",
        skill_ir_obj.contract_id, skill_ir_obj.version, skill_ir_obj.params, len(fileops),
    )

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
        "execution_mode": "graphir",
        "intent_fidelity": {"requested_skill": True, "executed_skill": True, "fallback_used": False},
    }
