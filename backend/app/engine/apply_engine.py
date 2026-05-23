"""Apply engine — GraphIR-only pipeline with intent coverage.

Executes a plan against a workspace using the GraphIR pipeline:
  SkillIR → Intent Coverage Check → IntentPlan (Intent-based)
  → GraphIRPipeline → GraphIRLayout
  → GraphIR Coverage Revalidation
  → ConstraintGraph (if enabled) or BackendRenderer
  → list[FileOp]
"""

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
from app.contracts.skill_registry import get_contract
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
    """Convert a SkillIR into an IntentPlan (legacy path, IntentNode-based).

    Temporary adapter until the LLM produces IntentPlan directly.
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


def _intents_to_intent_plan(intents: list[Intent], skill_ir: SkillIR) -> IntentPlan:
    """Convert decomposed Intent objects into an IntentPlan (new path).

    This is the intent-first path: intents → contract match.
    """
    contract = get_contract(skill_ir.contract_id, skill_ir.version)
    if contract is None:
        raise ValueError(f"Contract not found: {skill_ir.contract_id}@{skill_ir.version}")

    params = dict(skill_ir.params)

    return IntentPlan(
        intents=intents,
        contracts=[contract] if contract else [],
        params=params,
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
            "execution_mode": "graphir",
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
    requested_intents: list[dict],
    bound_nodes: dict[str, dict],
    rendered_entries: list[dict],
    skill_ir_obj,
) -> dict:
    """Compare requested → bound → rendered to measure semantic loss.

    Returns confidence-weighted loss report with three confidence levels:
    - source_confidence: from skill_ir decomposition confidence
    - pipeline_confidence: proportion of requested params present in bound
    - render_confidence: proportion of bound props present in rendered
    """
    # Flatten requested params across all intents
    requested_params: set[str] = set()
    for intent in requested_intents:
        params = intent.get("params", {})
        requested_params.update(params.keys())

    # Bound props: all keys from GraphIRNode.data dicts
    bound_params: set[str] = set()
    for node_id, data in bound_nodes.items():
        bound_params.update(data.keys())

    # Rendered props: all keys from _emit_log entries
    rendered_params: set[str] = set()
    for entry in rendered_entries:
        entry_props = entry.get("props", entry) if isinstance(entry, dict) else {}
        if isinstance(entry_props, dict):
            rendered_params.update(entry_props.keys())

    requested_to_bound_missing = requested_params - bound_params
    bound_to_rendered_missing = bound_params - rendered_params
    rendered_extra = rendered_params - bound_params

    source_conf = getattr(skill_ir_obj, 'confidence', None)
    if source_conf is None:
        source_conf = getattr(skill_ir_obj, 'decomposition_confidence', 1.0)
    source_confidence = float(source_conf) if isinstance(source_conf, (int, float)) else 1.0

    pipeline_confidence = 1.0
    if requested_params:
        covered = len(requested_params - requested_to_bound_missing)
        pipeline_confidence = covered / len(requested_params)

    render_confidence = 1.0
    if bound_params:
        rendered_covered = len(bound_params - bound_to_rendered_missing)
        render_confidence = rendered_covered / len(bound_params) if bound_params else 1.0

    return {
        "confidence": {
            "source_confidence": round(source_confidence, 4),
            "pipeline_confidence": round(pipeline_confidence, 4),
            "render_confidence": round(render_confidence, 4),
        },
        "requested_to_bound_loss": sorted(requested_to_bound_missing),
        "bound_to_rendered_loss": sorted(bound_to_rendered_missing),
        "rendered_extra": sorted(rendered_extra),
        "requested_param_count": len(requested_params),
        "bound_param_count": len(bound_params),
        "rendered_param_count": len(rendered_params),
    }


def _normalize(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def _build_audit(
    graph, intent_plan, fileops, exec_ctx, contract,
    skill_ir_obj, config,
    decisions=None, identities=None, resolved_mapping=None,
    file_nodes=None, workspace_root="", coverage_report=None,
    render_ctx=None, emit_log=None,
) -> dict:
    """Build run artifact audit with all 6 diagnostic sections + anomalies."""
    anomalies: list[dict] = []

    # ── Section 1: intent_realization ──
    requested_intents: list[dict] = []
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

    rendered_entries: list[dict] = list(emit_log) if emit_log else list(getattr(ReactBackend, '_emit_log', []))

    semantic_loss = _compute_semantic_loss(
        requested_intents, bound_nodes, rendered_entries, skill_ir_obj,
    )

    # ── Render coverage (node-level breadth + instance-level volume) ──
    emitted_node_ids: set[str] = set()
    emitted_instance_count = 0
    for entry in rendered_entries:
        if isinstance(entry, dict):
            nid = entry.get("node_id")
            if nid:
                emitted_node_ids.add(nid)
            emitted_instance_count += 1
    missing_nodes = sorted(set(graph.nodes.keys()) - emitted_node_ids)
    semantic_loss["render_coverage"] = {
        "expected_nodes": len(graph.nodes),
        "unique_nodes_emitted": len(emitted_node_ids),
        "total_emit_instances": emitted_instance_count,
        "missing_nodes": missing_nodes,
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
    ownership: dict = {}
    if file_nodes:
        for fp, fn in file_nodes.items():
            raw = getattr(fn, 'identity', None)
            if raw is not None:
                identity = raw.fingerprint() if hasattr(raw, 'fingerprint') else str(raw)
            else:
                identity = fp
            ownership[fp] = {"identity": identity}
    else:
        for f in contract.renderer.get("files", []):
            ownership[f.get("path", f.get("file", ""))] = {"identity": f.get("path", f.get("file", ""))}

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

    # Collect unresolved fragments from coverage
    if coverage_report:
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

    return {
        "intent_realization": {
            "requested": requested_intents,
            "bound": bound_nodes,
            "rendered": rendered_entries,
        },
        "component_identity": component_identity,
        "pipeline_route": pipeline_route,
        "ownership": ownership,
        "semantic_loss": semantic_loss,
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

    # ── Step 1: Check for decomposed intents (intent-first path) ──
    intents = _build_intents_from_plan(plan)
    coverage_report = None

    # Extract decomposition metadata from plan (produced by agent_plan.py)
    dec_info = plan.get("decomposition", {}) if isinstance(plan, dict) else {}

    if intents:
        # Intent-first path: build IntentPlan from decomposed intents
        try:
            intent_plan = _intents_to_intent_plan(intents, skill_ir_obj)
        except ValueError as e:
            return {"status": "rejected", "reason": str(e)}

        # Gate 2: Intent Coverage Check (uses ALL contracts, not just the selected one)
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
            # Use hard_coverage for gate: soft intents (layout.*, style.*)
            # never block the pipeline, they only degrade fidelity
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
        # Legacy path: _skill_ir_to_intent_plan (IntentNode-based)
        try:
            intent_plan = _skill_ir_to_intent_plan(skill_ir_obj)
        except ValueError as e:
            return {"status": "rejected", "reason": str(e)}

    # ── Step 2: GraphIR pipeline (build + layout + validate) ──
    try:
        graph, graph_layout = GraphIRPipeline.run(intent_plan)
    except ValueError as e:
        return {"status": "rejected", "reason": f"graphir:{e}"}

    # ── Step 2a: GraphIR Purity Check ──
    try:
        enforce_graph_purity(graph)
    except Exception as e:
        return {"status": "rejected", "reason": f"purity_violation:{e}"}

    # ── Step 3: Gate 4 — Coverage revalidation (intent-first only) ──
    if intents and coverage_report is not None:
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

    # ── Build audit ──
    audit = _build_audit(
        graph=graph,
        intent_plan=intent_plan,
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
    )

    _write_artifacts(
        context.artifacts, run_id, plan,
        [fop.to_dict() for fop in fileops],
        results, diff or "",
        audit=audit,
    )

    # ── Step 5: Build intent_fidelity ──
    if coverage_report is not None:
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
    else:
        # Legacy path — minimal fidelity info
        intent_fidelity = {
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
        "execution_mode": "graphir",
        "intent_fidelity": intent_fidelity,
        "audit": audit,
    }
