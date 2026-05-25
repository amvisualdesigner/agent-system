"""Apply engine — GraphIR-only pipeline with intent coverage.

Executes a plan against a workspace using the GraphIR pipeline.
Two modes:
  STRUCTURAL — StructuralIR authority (new path, no binding redistribution)
  LEGACY — IntentPlan-based (old path, with IntentCoverageValidator)
"""

from __future__ import annotations

import os
import json
import hashlib
import subprocess
import logging

from app.executor.patch_executor_dumb import apply_dumb as apply_dumb_op
from app.policy.policy import validate_plan_policy, validate_operation
from app.utils.state import write_state
from app.executor.diff_generator import generate_diff
from app.utils.path_guard import guard_within
from app.config.settings import settings
from app.config.feature_flags import FEATURE_FLAGS
from app.contracts.skill_ir import SkillIR
from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.contract_resolution import ContractResolution
from app.contracts.skill_registry import get_contract
from app.engine.structural_completion import (
    complete_structure,
    graphir_ready_to_intent_plan,
    StructuralIR,
)
from app.graphir.structural_coverage import (
    StructuralCoverageValidator,
    StructuralIntegrityError,
    StructuralCoverageReport,
)
from app.graphir.intent import Intent, IntentPlan, is_capability_metadata
from app.graphir.intent_coverage import IntentCoverageValidator, IntentCoverageError
from app.graphir.builder import GraphIRBuilder
from app.graphir.pipeline import GraphIRPipeline
from app.graphir.validator import GraphIRValidator
from app.graphir.backends import ReactBackend, BackendConfig
from app.graphir.utils import validate_fileops
from app.graphir.utils import extract_component_name
from app.graphir.utils import check_repo_integrity
from app.graphir.boundary import enforce_graph_purity
from app.graphir.constraint import ExecutionContext
from app.graphir.constraint.validation import shadow_compare, shadow_compare_decisions

logger = logging.getLogger(__name__)


def _build_intents_from_plan(plan: dict) -> list[Intent] | None:
    """Extract Intent objects from the plan dict if present."""
    raw = plan.get("intents") if isinstance(plan, dict) else None
    if not raw:
        return None
    return [Intent.from_dict(item) for item in raw]


def _skill_ir_to_intent_plan(skill_ir: SkillIR) -> IntentPlan:
    """Convert a SkillIR into an IntentPlan (LEGACY path, IntentNode-based).

    DEPRECATED — will be removed after reconciliation validation.
    New code MUST use _intents_to_intent_plan with SemanticResolution.
    """
    import warnings
    warnings.warn(
        "_skill_ir_to_intent_plan is deprecated. "
        "Use _intents_to_intent_plan with SemanticResolution instead.",
        DeprecationWarning,
        stacklevel=2,
    )
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
    from app.graphir.intent import IntentNode
    intents: list[IntentNode] = []

    file_count = len(contract.renderer.get("files", []))
    if file_count > 0:
        intents.append(IntentNode(type="PAGE", params={}))

    for slot in slots:
        intent_type = _resolve_intent_type(slot.get("type", ""))
        props = _resolve_props(slot.get("props", {}), resolved_params)
        intents.append(IntentNode(type=intent_type, params=props))

    return IntentPlan(
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


def _compare_graphs_structural(graph, shadow_graph) -> list[dict]:
    """Compare structural graph vs legacy shadow graph for structural equivalence.

    Both graphs should have the same node set with identical params when
    the StructuralIR → IntentPlan projection is correct.

    Returns:
        List of divergence dicts (empty = structurally equivalent).
    """
    divergences: list[dict] = []

    s_nodes = set(graph.nodes.keys())
    l_nodes = set(shadow_graph.nodes.keys())

    if s_nodes != l_nodes:
        divergences.append({
            "type": "node_set_mismatch",
            "structural_only": sorted(s_nodes - l_nodes),
            "legacy_only": sorted(l_nodes - s_nodes),
        })

    if not divergences:
        for node_id in s_nodes:
            s_node = graph.nodes[node_id]
            l_node = shadow_graph.nodes[node_id]
            s_data = getattr(s_node, 'data', {}) or {}
            l_data = getattr(l_node, 'data', {}) or {}
            if s_data != l_data:
                divergences.append({
                    "type": "param_mismatch",
                    "node": node_id,
                    "structural": dict(s_data),
                    "legacy": dict(l_data),
                })

    return divergences
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


def _write_artifacts(artifacts_dir: str, run_id: str, plan: dict, operations: list, results: list, diff: str,
                     audit: dict | None = None):
    with open(f"{artifacts_dir}/plan.json", "w") as f:
        json.dump(plan, f, indent=2)
    with open(f"{artifacts_dir}/execution.json", "w") as f:
        payload: dict = {"run_id": run_id, "operations": operations, "results": results}
        if audit:
            payload["audit"] = audit
        json.dump(payload, f, indent=2)
    with open(f"{artifacts_dir}/summary.json", "w") as f:
        summary = {
            "run_id": run_id,
            "status": "ok",
            "files_created": [r.get("path") for r in results if r.get("status") == "created"],
            "execution_mode": FEATURE_FLAGS.get("execution_mode", "graphir"),
            "trace_level": FEATURE_FLAGS.get("trace_level", "full"),
        }
        if audit:
            summary["anomalies"] = audit.get("anomalies", [])
        json.dump(summary, f, indent=2)
    with open(f"{artifacts_dir}/diff.patch", "w") as f:
        f.write(diff)


def _run_constraint_pipeline(
    graph, graph_layout, exec_ctx, backend_config,
    *, shadow_mode: bool = False,
) -> tuple[list, dict, dict, dict, list]:
    """Run the full Constraint Graph pipeline.

    When *shadow_mode* is True, all persistence is blocked
    (memory.save, audit logs, metrics, caches, semantic state).

    Returns:
        (fileops, decisions, identities, split_plan, deletions)
    """
    from app.graphir.constraint.indexer import RepositoryIndexer
    from app.graphir.constraint.matcher import IntentFileMatcher
    from app.graphir.constraint.resolver import IdentityResolver
    from app.graphir.constraint.renderer import RepositoryAwareRenderer
    from app.graphir.constraint.memory import RepositorySemanticMemory
    from app.graphir.constraint.crl import ConflictResolutionLayer
    from app.graphir.constraint.split_analyzer import SPLITAnalyzer
    from app.graphir.constraint.deletion import detect_deletions
    from app.graphir.constraint.models import MemoryRecord
    from app.graphir.constraint.context import PipelineState, RenderContext

    indexer = RepositoryIndexer()
    file_nodes, component_nodes = indexer.index(exec_ctx.workspace_root)

    memory = RepositorySemanticMemory(exec_ctx.memory_path)
    raw_memory = memory.load()

    # CRL operates on dict[str, str]; adapt MemoryRecord → file_path
    crl_input = {fp: rec.file_path for fp, rec in raw_memory.items()}
    crl = ConflictResolutionLayer()
    cleaned_paths, crl_conflicts = crl.resolve(crl_input, file_nodes)

    # Rebuild MemoryRecord dict from cleaned paths
    resolved_mapping: dict[str, MemoryRecord] = {}
    for fp, file_path in cleaned_paths.items():
        rec = raw_memory.get(fp)
        if rec is None:
            logger.warning(
                "Memory corruption: fingerprint %s missing MemoryRecord — skipping", fp,
            )
            continue
        resolved_mapping[fp] = MemoryRecord(
            fingerprint=fp,
            file_path=file_path,
            component_name=rec.component_name,
        )

    matcher = IntentFileMatcher()
    resolver = IdentityResolver(resolved_mapping=resolved_mapping)
    renderer = RepositoryAwareRenderer()

    identities, candidates = matcher.match(graph, file_nodes)
    decisions = resolver.resolve(identities, candidates, file_nodes)

    split_analyzer = SPLITAnalyzer()
    split_plan = split_analyzer.analyze(
        decisions, identities, file_nodes, crl_conflicts,
    )

    # Phase 6a: DELETE detection via state difference
    deletions = detect_deletions(resolved_mapping, identities, file_nodes)

    pipeline_state = PipelineState(
        file_nodes=file_nodes,
        component_nodes=component_nodes,
        decisions=decisions,
        split_plan=split_plan,
        deletions=deletions,
        resolved_mapping=resolved_mapping,
        exec_ctx=exec_ctx,
    )
    render_ctx = RenderContext(
        execution=pipeline_state,
        feature_flags=dict(FEATURE_FLAGS),
    )

    fileops = renderer.render(
        graph, graph_layout, backend_config,
        context=render_ctx,
    )

    if not shadow_mode:
        deleted_fps = {d.fingerprint for d in deletions}
        updated = memory.merge(
            decisions, identities, resolved_mapping,
            deleted_fingerprints=deleted_fps,
        )
        memory.save(updated)

    return fileops, decisions, identities, split_plan, deletions


def _compute_semantic_loss(
    bound_nodes: dict[str, dict],
) -> dict:
    """Measure semantic loss — pure binding fidelity.

    StructuralIR is truth. No SkillIR comparison.
    Param provenance is already in trace.semantic.param_provenance.

    Returns:
        intent_binding_loss: params that reached each bound node
    """
    binding_per_node = {}
    for node_id, node_data in (bound_nodes or {}).items():
        data = node_data.get("data", {}) if isinstance(node_data, dict) else {}
        if data is None:
            data = {}
        binding_per_node[node_id] = {
            "bound_param_count": len(data),
            "bound_params": sorted(data.keys()),
        }
    return {"intent_binding_loss": binding_per_node}


def build_ownership(file_nodes, contract) -> dict:
    """Unified ownership builder — same shape regardless of pipeline path.

    Always returns {file_path: {"identity": str, "source": "index"|"contract"}}
    """
    ownership: dict = {}
    if file_nodes:
        for fp, fn in file_nodes.items():
            raw = getattr(fn, 'identity', None)
            identity = (
                raw.fingerprint()
                if raw is not None and hasattr(raw, 'fingerprint')
                else fp
            )
            ownership[fp] = {"identity": identity, "source": "index"}
    else:
        for f in contract.renderer.get("files", []):
            fp = f.get("path", f.get("file", ""))
            ownership[fp] = {"identity": fp, "source": "contract"}
    return ownership


def _normalize(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def _build_audit(
    graph, intent_plan, fileops, exec_ctx, contract,
    skill_ir_obj, config,
    decisions=None, identities=None, resolved_mapping=None,
    file_nodes=None, workspace_root="", coverage_report=None,
    render_ctx=None, emit_log=None,
    structural_ir=None, sreport=None,
    shadow_divergences=None,
) -> dict:
    """Build run artifact audit with all diagnostic sections + anomalies.

    Two modes:
      NEW PATH (structural_ir provided): layered trace (semantic, structural, ui, execution)
      LEGACY PATH (structural_ir=None): flat intent_realization section
    """
    anomalies: list[dict] = []

    # ── Section 1: intent/semantic layer ──
    requested_intents: list[dict] = []

    if structural_ir is not None:
        # NEW PATH: build from StructuralIR capabilities (no legacy intents)
        from app.engine.structural_completion import CompletionMode
        for rc in structural_ir.capabilities:
            if rc.mode != CompletionMode.SAFE_SKIP:
                requested_intents.append({
                    "type": rc.name,
                    "params": dict(rc.params),
                    "mode": rc.mode.value if hasattr(rc.mode, 'value') else str(rc.mode),
                })
    elif intent_plan is not None:
        # LEGACY PATH: build from IntentPlan
        for intent in intent_plan.intents:
            if hasattr(intent, 'capability'):
                requested_intents.append({
                    "type": intent.capability,
                    "params": dict(intent.params) if hasattr(intent, 'params') else {},
                    "weight": getattr(intent, 'weight', 1.0),
                })
            elif hasattr(intent, 'type'):
                requested_intents.append({
                    "type": intent.type,
                    "params": dict(intent.params) if hasattr(intent, 'params') else {},
                })
            elif isinstance(intent, dict):
                requested_intents.append(intent)

    bound_nodes: dict[str, dict] = {}
    for node_id, node in graph.nodes.items():
        if hasattr(node, 'data') and node.data:
            bound_nodes[node_id] = {
                "type": getattr(node, 'type', 'unknown'),
                "data": dict(node.data),
            }

    semantic_loss = _compute_semantic_loss(
        bound_nodes,
    )

    # ── Render coverage — derived from RenderTrace ONLY ──
    traces = getattr(ReactBackend, '_render_traces', [])
    entered_set = {t.node_id for t in traces if t.phase == "entered"}
    emitted_set = {t.node_id for t in traces if t.phase == "emitted"}
    render_coverage = {
        "expected_nodes": len(graph.nodes),
        "nodes_entered": sum(1 for t in traces if t.phase == "entered"),
        "unique_nodes_entered": len(entered_set),
        "nodes_emitted": sum(1 for t in traces if t.phase == "emitted"),
        "unique_nodes_emitted": len(emitted_set),
        "missing_nodes": sorted(set(graph.nodes.keys()) - entered_set),
        "entered_no_emit": sorted(entered_set - emitted_set),
    }

    # ── Section 2: component_identity ──
    component_identity: dict = {}
    if identities:
        for intent_id, ci in identities.items():
            identity_str = ci.fingerprint() if hasattr(ci, 'fingerprint') else str(ci)
            component_identity[str(intent_id)] = identity_str
    else:
        for fop in fileops:
            component_identity[fop.path] = {"action": fop.action}

    # ── Section 3: pipeline_route ──
    route = exec_ctx.active_route if exec_ctx else "unknown"
    pipeline_route: dict = {
        "route": route,
        "constraint_graph": bool(FEATURE_FLAGS.get("constraint_graph", False)),
        "constraint_graph_shadow": bool(FEATURE_FLAGS.get("constraint_graph_shadow", True)),
        "constraint_graph_line_range": bool(FEATURE_FLAGS.get("constraint_graph_line_range", False)),
    }

    # Detect route anomalies
    if route == "unknown":
        anomalies.append({
            "severity": "warning",
            "type": "route_unknown",
            "detail": "pipeline route was not set (defaulted to 'unknown')",
        })

    # ── Section 4: ownership ──
    ownership = build_ownership(file_nodes, contract)

    # ── Section 5: semantic_loss ──
    # Already computed above

    # ── Section 6: repo_integrity ──
    repo_root = workspace_root or (exec_ctx.workspace_root if exec_ctx else "")
    repo_integrity = check_repo_integrity(repo_root)

    # mutation_state: check if written files drifted from emitted content
    mutation_state = "clean"
    for fop in fileops:
        if not repo_root:
            continue
        fpath = os.path.join(repo_root, fop.path)
        if os.path.exists(fpath):
            try:
                with open(fpath) as fh:
                    if _normalize(fh.read()) != _normalize(fop.content):
                        mutation_state = "drifted"
                        break
            except Exception:
                mutation_state = "drifted"
                break
    repo_integrity["mutation_state"] = mutation_state
    if mutation_state == "drifted":
        anomalies.append({
            "severity": "warning",
            "type": "file_drift",
            "detail": "Written file differs from emitted content — possible external modification",
        })

    if repo_integrity.get("issues"):
        for issue in repo_integrity["issues"]:
            anomalies.append({
                "severity": issue.get("severity", "warning"),
                "type": issue.get("type", "integrity"),
                "detail": issue.get("detail", ""),
                "file": issue.get("file"),
            })

    # Collect unresolved fragments from coverage (LEGACY only)
    if coverage_report and structural_ir is None:
        uf = getattr(coverage_report, 'unresolved_fragments', None)
        if uf:
            anomalies.append({
                "severity": "warning",
                "type": "unresolved_fragments",
                "detail": f"{len(uf)} unresolved fragments in coverage report",
            })
        missing = getattr(coverage_report, 'missing', None)
        if missing:
            for m in missing:
                anomalies.append({
                    "severity": "info",
                    "type": "missing_intent",
                    "detail": getattr(m, 'reason', str(m)),
                    "capability": getattr(m, 'capability', 'unknown'),
                })

    # ── Shadow validation anomaly (structural ↔ legacy divergence) ──
    if shadow_divergences:
        anomalies.append({
            "severity": "warning",
            "type": "structural_shadow_divergence",
            "detail": f"{len(shadow_divergences)} divergences between structural and legacy graph",
            "divergences": shadow_divergences,
        })

    if structural_ir is not None:
        # NEW PATH: layered trace
        output = {
            "trace": {
                "semantic": {
                    "resolution": {
                        "contract_id": structural_ir.contract_id,
                        "contract_version": structural_ir.contract_version,
                        "confidence": structural_ir.confidence,
                    },
                    "param_provenance": structural_ir.param_provenance,
                },
                "structural": {
                    "capabilities": [
                        {
                            "name": rc.name,
                            "params": dict(rc.params),
                            "mode": rc.mode.value if hasattr(rc.mode, 'value') else str(rc.mode),
                        }
                        for rc in structural_ir.capabilities
                    ],
                    "coverage": {
                        "completeness": sreport.completeness if sreport else 1.0,
                        "safe_skip_count": sreport.safe_skip_count if sreport else 0,
                        "warnings": sreport.warnings if sreport else [],
                    },
                    "completion_warnings": structural_ir.completion_warnings,
                },
                "ui": {
                    "graph": {
                        "nodes": list(graph.nodes.keys()),
                        "edges": len(graph.edges),
                    },
                    "render_coverage": render_coverage,
                },
                "execution": {
                    "bound": bound_nodes,
                    "semantic_loss": semantic_loss,
                    "rendered": list(emit_log) if emit_log else [],
                },
            },
            "component_identity": component_identity,
            "pipeline_route": pipeline_route,
            "ownership": ownership,
            "repo_integrity": repo_integrity,
            "anomalies": anomalies,
        }
        if shadow_divergences is not None:
            output["trace"]["validation"] = {
                "shadow": {
                    "mode": "structural_vs_legacy",
                    "divergences": shadow_divergences,
                    "equivalent": len(shadow_divergences) == 0,
                },
            }
        return output

    # ── LEGACY PATH: flat sections ──
    return {
        "intent_realization": {
            "requested": requested_intents,
            "bound": bound_nodes,
            "rendered": list(emit_log) if emit_log else [],
        },
        "component_identity": component_identity,
        "pipeline_route": pipeline_route,
        "ownership": ownership,
        "semantic_loss": semantic_loss,
        "render_coverage": render_coverage,
        "repo_integrity": repo_integrity,
        "anomalies": anomalies,
    }


def apply_engine(run_id, plan: dict, context, dry_run: bool = False, compiler_mode: str = "strict"):
    """Execute a plan against a workspace using the GraphIR pipeline.

    Pipeline (intent-first path):
      1. Extract intents from plan (if present)
      2. Convert SkillIR → IntentPlan
      3. Run coverage check (Gate 2)
      4. GraphIRPipeline: IntentPlan → (GraphIR, GraphIRLayout)
      5. Coverage revalidation (Gate 4)
      6. ConstraintGraph (if enabled) or BackendRenderer → FileOps
      7. Apply → git commit
    """
    guard_within(context.workspace, settings.RUNS_DIR)
    guard_within(context.artifacts, settings.ARTIFACTS_DIR)
    os.makedirs(context.artifacts, exist_ok=True)

    # ── Build ExecutionContext ──
    exec_ctx = ExecutionContext(
        run_id=run_id,
        workspace_root=context.workspace,
        worktree_id=f"agent-{run_id[:8]}",
        artifacts_dir=context.artifacts,
        active_route="constraint" if FEATURE_FLAGS.get("constraint_graph", False) else "legacy",
    )

    skill_ir = plan.get("skill_ir")
    if not skill_ir:
        return {"status": "rejected", "reason": "no_skill_ir"}

    skill_ir_obj = SkillIR.from_dict(skill_ir)
    contract = get_contract(skill_ir_obj.contract_id, skill_ir_obj.version)
    if contract is None:
        return {"status": "rejected", "reason": f"contract_not_found:{skill_ir_obj.contract_id}"}

    # ── Step 1: Determine pipeline mode and build execution plan ──
    pipeline_mode = "STRUCTURAL" if plan.get("semantic_frame") else "LEGACY"

    structural_ir: StructuralIR | None = None
    sreport: StructuralCoverageReport | None = None
    intents: list[Intent] | None = None
    intent_plan: IntentPlan | None = None
    coverage_report = None

    if pipeline_mode == "STRUCTURAL":
        # ── NEW PATH: StructuralIR authority ──
        # No intents, no decomposition, no flattening, no IntentPlan artifact.
        try:
            from app.engine.reconciliation import reconcile
            from app.contracts.semantic_resolution import SemanticConflictError

            semantic_frame = plan["semantic_frame"]

            # Phase 1: Pure semantic from language
            semantic_resolution = reconcile(semantic_frame, skill_ir_obj)

            # Phase 2: Contract adaptation from SkillIR proposal
            contract_resolution = ContractResolution.from_skillir(skill_ir_obj, contract)

            # Phase 3: StructuralIR (merges both + slot mapping)
            structural_ir = complete_structure(
                semantic_resolution, contract_resolution, contract, semantic_frame,
            )

            # Gate 2: StructuralCoverageValidator (replaces IntentCoverageValidator)
            sreport = StructuralCoverageValidator.validate(structural_ir, contract)
        except (ValueError, SemanticConflictError, StructuralIntegrityError) as e:
            return {"status": "rejected", "reason": f"structural:{e}"}

    else:
        # ── LEGACY PATH: Intent-based ──
        intents = _build_intents_from_plan(plan)

        # Extract decomposition metadata from plan (produced by agent_plan.py)
        dec_info = plan.get("decomposition", {}) if isinstance(plan, dict) else {}

        if intents:
            # Intent-first legacy: reconcile → ContractResolution → IntentPlan
            try:
                from app.engine.reconciliation import reconcile
                from app.contracts.semantic_resolution import SemanticConflictError

                semantic_frame = plan.get("semantic_frame") if isinstance(plan, dict) else None
                if semantic_frame:
                    semantic_resolution = reconcile(semantic_frame, skill_ir_obj)
                else:
                    semantic_resolution = SemanticResolution(
                        semantic_params={}, semantic_provenance={}, confidence=0.0,
                    )

                contract_resolution = ContractResolution.from_skillir(skill_ir_obj, contract)

                structural_ir = complete_structure(
                    semantic_resolution, contract_resolution, contract, semantic_frame,
                )
                intent_plan = graphir_ready_to_intent_plan(structural_ir)
            except (ValueError, SemanticConflictError) as e:
                return {"status": "rejected", "reason": str(e)}

            # Gate 2: Intent Coverage Check (uses ALL contracts)
            try:
                from app.contracts.skill_registry import SKILL_CONTRACTS
                all_contracts = list(SKILL_CONTRACTS.values())
                validator = IntentCoverageValidator()
                coverage_report = validator.check_coverage(
                    intents,
                    all_contracts,
                    task=plan.get("task", ""),
                    detected_intents=dec_info.get("detected"),
                    inferred_intents=dec_info.get("inferred"),
                    unresolved_fragments=dec_info.get("unresolved"),
                    decomposition_confidence=dec_info.get("decomposition_confidence"),
                )
                if coverage_report.hard_coverage < 1.0:
                    return {
                        "status": "rejected",
                        "reason": "intent_coverage_failure",
                        "coverage": {
                            "coverage": coverage_report.coverage,
                            "hard_coverage": coverage_report.hard_coverage,
                            "missing_intents": [
                                {"capability": m.capability, "reason": m.reason}
                                for m in coverage_report.missing
                            ],
                        },
                    }
                coverage_report = coverage_report.with_gate("coverage", True)
            except Exception as e:
                return {"status": "rejected", "reason": f"coverage_error:{e}"}
        else:
            # Legacy path: _skill_ir_to_intent_plan (IntentNode-based, DEPRECATED)
            try:
                intent_plan = _skill_ir_to_intent_plan(skill_ir_obj)
            except ValueError as e:
                return {"status": "rejected", "reason": str(e)}

    # ── Step 2: GraphIR pipeline (build + layout + validate) ──
    try:
        if pipeline_mode == "STRUCTURAL":
            graph, graph_layout = GraphIRPipeline.run_from_structural(structural_ir)
        else:
            graph, graph_layout = GraphIRPipeline.run(intent_plan)
    except ValueError as e:
        return {"status": "rejected", "reason": f"graphir:{e}"}

    # ── Step 2a: GraphIR Purity Check ──
    try:
        enforce_graph_purity(graph)
    except Exception as e:
        return {"status": "rejected", "reason": f"purity_violation:{e}"}

    # ── Step 2b: Structural shadow validation ──
    # Runs legacy GraphIRPipeline.run() in shadow mode to compare
    # structural vs legacy graph equivalence. Validates that the
    # StructuralIR → IntentPlan projection is lossless.
    shadow_divergences = None
    emit_assertions = FEATURE_FLAGS.get("emit_structural_assertions", False)
    if pipeline_mode == "STRUCTURAL" and emit_assertions:
        try:
            shadow_intent_plan = graphir_ready_to_intent_plan(structural_ir)
            shadow_graph, _ = GraphIRPipeline.run(shadow_intent_plan)
            shadow_divergences = _compare_graphs_structural(graph, shadow_graph)
            if shadow_divergences:
                logger.warning(
                    "STRUCTURAL SHADOW: %d divergences — structural ↔ legacy graph mismatch",
                    len(shadow_divergences),
                )
                for div in shadow_divergences:
                    logger.warning("  SHADOW divergence: %s", div)
        except Exception as e:
            shadow_divergences = [{"type": "shadow_failed", "error": str(e)}]
            logger.warning("STRUCTURAL SHADOW: validation failed — %s", e)

    # ── Step 3: Gate 4 — Coverage revalidation (LEGACY path only) ──
    if pipeline_mode == "LEGACY" and intents and coverage_report is not None:
        try:
            coverage_report = IntentCoverageValidator.revalidate(graph, coverage_report, intents)
            if not coverage_report.is_complete:
                return {
                    "status": "rejected",
                    "reason": "intent_revalidation_failure",
                    "coverage": {
                        "coverage": coverage_report.coverage,
                        "uncovered_intents": coverage_report.uncovered_intents,
                    },
                }
        except IntentCoverageError as e:
            return {"status": "rejected", "reason": str(e)}

    # ── Step 4: ConstraintGraph or BackendRenderer ──
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

    # ── Audit var holders (collected from whichever path runs) ──
    _audit_decisions: dict | None = None
    _audit_identities: dict | None = None
    _audit_resolved_mapping: dict | None = None
    _audit_file_nodes: dict | None = None
    _audit_render_ctx = None

    if FEATURE_FLAGS.get("constraint_graph", False):
        # New ConstraintGraph pipeline (Phases 1+)
        from app.graphir.constraint.indexer import RepositoryIndexer
        from app.graphir.constraint.matcher import IntentFileMatcher
        from app.graphir.constraint.resolver import IdentityResolver
        from app.graphir.constraint.renderer import RepositoryAwareRenderer
        from app.graphir.constraint.memory import RepositorySemanticMemory
        from app.graphir.constraint.crl import ConflictResolutionLayer
        from app.graphir.constraint.split_analyzer import SPLITAnalyzer
        from app.graphir.constraint.deletion import detect_deletions
        from app.graphir.constraint.models import MemoryRecord
        from app.graphir.constraint.context import PipelineState, RenderContext

        indexer = RepositoryIndexer()
        file_nodes, component_nodes = indexer.index(exec_ctx.workspace_root)

        # Phase 2: Load persistent semantic memory → feed resolver
        memory = RepositorySemanticMemory(exec_ctx.memory_path)
        raw_memory = memory.load()

        # Phase 3: Reconcile memory against current file state
        # CRL operates on dict[str, str]; adapt MemoryRecord → file_path
        crl_input = {fp: rec.file_path for fp, rec in raw_memory.items()}
        crl = ConflictResolutionLayer()
        cleaned_paths, crl_conflicts = crl.resolve(crl_input, file_nodes)

        # Rebuild MemoryRecord dict from cleaned paths
        resolved_mapping: dict[str, MemoryRecord] = {}
        for fp, file_path in cleaned_paths.items():
            rec = raw_memory.get(fp)
            if rec is None:
                logger.warning(
                    "Memory corruption: fingerprint %s missing MemoryRecord — skipping", fp,
                )
                continue
            resolved_mapping[fp] = MemoryRecord(
                fingerprint=fp,
                file_path=file_path,
                component_name=rec.component_name,
            )

        matcher = IntentFileMatcher()
        resolver = IdentityResolver(resolved_mapping=resolved_mapping)
        renderer = RepositoryAwareRenderer()

        # Pre-compute decisions (needed for memory persistence)
        identities, candidates = matcher.match(graph, file_nodes)
        decisions = resolver.resolve(identities, candidates, file_nodes)

        # Phase 4: Structural analysis — detect overloaded files
        split_analyzer = SPLITAnalyzer()
        split_plan = split_analyzer.analyze(
            decisions, identities, file_nodes, crl_conflicts,
        )

        # Phase 6a: DELETE detection via state difference
        deletions = detect_deletions(resolved_mapping, identities, file_nodes)

        pipeline_state = PipelineState(
            file_nodes=file_nodes,
            component_nodes=component_nodes,
            decisions=decisions,
            split_plan=split_plan,
            deletions=deletions,
            resolved_mapping=resolved_mapping,
            exec_ctx=exec_ctx,
        )
        render_ctx = RenderContext(
            execution=pipeline_state,
            feature_flags=dict(FEATURE_FLAGS),
        )

        ReactBackend.reset_emit_log(run_id)
        ReactBackend.reset_traces(run_id)
        fileops = renderer.render(
            graph, graph_layout, backend_config,
            context=render_ctx,
        )

        # Collect constraint vars for audit
        _audit_decisions = decisions
        _audit_identities = identities
        _audit_resolved_mapping = resolved_mapping
        _audit_file_nodes = file_nodes
        _audit_render_ctx = render_ctx

        # Phase 2: Persist new identity→file mappings
        deleted_fps = {d.fingerprint for d in deletions}
        updated = memory.merge(
            decisions, identities, resolved_mapping,
            deleted_fingerprints=deleted_fps,
        )
        memory.save(updated)
    else:
        # Legacy path: direct BackendRenderer (unchanged behavior)
        ReactBackend.reset_emit_log(run_id)
        ReactBackend.reset_traces(run_id)
        backend = ReactBackend()
        fileops = backend.render(graph, graph_layout, backend_config)

    # ── Capture emitted props right after primary render (before shadow) ──
    _captured_emit_log = list(ReactBackend._emit_log.get(run_id, []))

    # ── Shadow mode: run constraint pipeline alongside legacy ──
    # Only runs when constraint_graph=False (primary path is legacy).
    # When constraint_graph=True, the constraint pipeline is already primary — no shadow needed.
    if FEATURE_FLAGS.get("constraint_graph_shadow", True) and not FEATURE_FLAGS.get("constraint_graph", False):
        prev_line_range = FEATURE_FLAGS.get("constraint_graph_line_range", False)
        try:
            FEATURE_FLAGS["constraint_graph_line_range"] = True

            shadow_fileops, shadow_decisions, shadow_identities, shadow_split, shadow_deletions = (
                _run_constraint_pipeline(
                    graph, graph_layout, exec_ctx, backend_config,
                    shadow_mode=True,
                )
            )

            # Compare decisions pre-render (Phase 6a)
            shadow_compare_decisions(decisions, shadow_decisions, run_id)

            # Compare final FileOps (does NOT affect fileops)
            shadow_compare(fileops, shadow_fileops, run_id)

        except Exception as e:
            logger.warning(
                "SHADOW: constraint pipeline failed — %s (run=%s)",
                e, run_id,
            )
        finally:
            FEATURE_FLAGS["constraint_graph_line_range"] = prev_line_range

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

    # ── Step 4a: Filter plan for artifacts (remove decomposition intents in new path) ──
    if pipeline_mode == "STRUCTURAL":
        clean_plan = {k: v for k, v in plan.items() if k not in ("intents", "decomposition")}
    else:
        clean_plan = plan

    # ── Build audit ──
    audit = _build_audit(
        graph=graph,
        intent_plan=intent_plan,
        structural_ir=structural_ir,
        sreport=sreport,
        fileops=fileops,
        exec_ctx=exec_ctx,
        contract=contract,
        skill_ir_obj=skill_ir_obj,
        config=backend_config,
        decisions=_audit_decisions,
        identities=_audit_identities,
        resolved_mapping=_audit_resolved_mapping,
        file_nodes=_audit_file_nodes,
        workspace_root=context.workspace,
        coverage_report=coverage_report,
        render_ctx=_audit_render_ctx,
        emit_log=_captured_emit_log,
        shadow_divergences=shadow_divergences,
    )

    _write_artifacts(
        context.artifacts, run_id, clean_plan,
        [fop.to_dict() for fop in fileops],
        results, diff or "",
        audit=audit,
    )

    # ── Step 5: Build fidelity report ──
    if pipeline_mode == "STRUCTURAL":
        structural_fidelity = {
            "completeness": sreport.completeness,
            "safe_skip_count": sreport.safe_skip_count,
            "total_capabilities": len(structural_ir.capabilities),
            "warnings": sreport.warnings,
            "execution_mode": FEATURE_FLAGS.get("execution_mode", "graphir"),
            "trace_level": FEATURE_FLAGS.get("trace_level", "full"),
        }
        if shadow_divergences is not None:
            structural_fidelity["shadow_validation"] = {
                "divergences": len(shadow_divergences),
                "equivalent": len(shadow_divergences) == 0,
            }
        fidelity = structural_fidelity
    elif coverage_report is not None:
        intent_fidelity = {
            "coverage": coverage_report.coverage,
            "hard_coverage": coverage_report.hard_coverage,
            "total_intents": coverage_report.total_intents,
            "covered_intents": coverage_report.covered_intents,
            "decomposition_confidence": coverage_report.decomposition_confidence,
            "semantic_entropy": coverage_report.semantic_entropy,
            "detected_intents": coverage_report.detected_intents,
            "inferred_intents": coverage_report.inferred_intents,
            "unresolved_fragments": coverage_report.unresolved_fragments,
            "matched_intents": [m.capability for m in coverage_report.matched],
            "missing_intents": [
            {"capability": m.capability, "reason": m.reason}
            for m in coverage_report.missing
            if not is_capability_metadata(m.capability)
        ],
            "uncovered_intents": coverage_report.uncovered_intents,
            "fallback_used": coverage_report.fallback_used,
        }
        fidelity = intent_fidelity
    else:
        # Legacy path — minimal fidelity info
        fidelity = {
            "coverage": 1.0,
            "hard_coverage": 1.0,
            "total_intents": len(intent_plan.intents),
            "covered_intents": len(intent_plan.intents),
            "decomposition_confidence": 1.0,
            "semantic_entropy": 0.0,
            "detected_intents": [],
            "inferred_intents": [],
            "unresolved_fragments": [],
            "matched_intents": [getattr(i, 'capability', getattr(i, 'type', 'unknown')) for i in intent_plan.intents],
            "missing_intents": [],
            "uncovered_intents": [],
            "fallback_used": False,
        }

    write_state(run_id, "apply")

    return {
        "status": "ok",
        "run_id": run_id,
        "dry_run": dry_run,
        "operations": [fop.to_dict() for fop in fileops],
        "execution": results,
        "workspace": context.workspace,
        "execution_mode": FEATURE_FLAGS.get("execution_mode", "graphir"),
        "trace_level": FEATURE_FLAGS.get("trace_level", "full"),
        "fidelity": fidelity,
        "audit": audit,
    }
