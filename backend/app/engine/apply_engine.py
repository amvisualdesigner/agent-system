"""Apply engine — StructuralIR-only pipeline.

Executes a plan against a workspace using the GraphIR pipeline.
Single pipeline mode: StructuralIR authority.
"""

from __future__ import annotations

import os
import json
import hashlib
import subprocess
import logging
from functools import lru_cache

from app.graphir.constraint.executor import FileOpApplier
from app.policy.policy import validate_plan_policy, validate_operation
from app.state.run_state import save_run_state
from app.executor.diff_generator import generate_diff
from app.utils.path_guard import guard_within
from app.config.settings import settings
from app.config.feature_flags import FEATURE_FLAGS
from app.contracts.skill_ir import SkillIR
from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.contract_resolution import ContractResolution
from app.contracts.skill_registry import get_contract
from app.engine.errors import AmbiguousStructuralTargetError
from app.engine.structural_completion import (
    complete_structure,
    StructuralIR,
    SubstitutionOp,
)
from app.engine.structural_index import StructuralIndex
from app.graphir.structure.models import StructuralResolution
from app.graphir.structural_coverage import (
    StructuralCoverageValidator,
    StructuralIntegrityError,
    StructuralCoverageReport,
)
from app.graphir.compiler import MISSING_REQUIRED_PROPS
from app.binding.resolver import resolve as resolve_bindings
from app.graphir.pipeline import GraphIRPipeline
from app.graphir.backends import ReactBackend, BackendConfig
from app.graphir.utils import validate_fileops, FileOp
from app.graphir.utils import extract_component_name
from app.graphir.utils import check_repo_integrity
from app.intent.models import FallbackExecutionRequest, RefactorChange
from app.graphir.boundary import enforce_graph_purity
from app.graphir.constraint import ExecutionContext

logger = logging.getLogger(__name__)


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
                     audit: dict | None = None, fidelity: dict | None = None,
                     verify: dict | None = None):
    with open(f"{artifacts_dir}/plan.json", "w") as f:
        json.dump(plan, f, indent=2)
    with open(f"{artifacts_dir}/execution.json", "w") as f:
        json.dump({
            "status": "ok",
            "diff": diff or None,
            "operations": operations,
        }, f, indent=2)
    with open(f"{artifacts_dir}/context.json", "w") as f:
        json.dump({
            "run_id": run_id,
            "execution_mode": FEATURE_FLAGS.get("execution_mode", "graphir"),
            "trace_level": FEATURE_FLAGS.get("trace_level", "full"),
            "repo_snapshot": [op.get("path") for op in operations if "path" in op],
        }, f, indent=2)
    with open(f"{artifacts_dir}/meta.json", "w") as f:
        payload = {}
        if audit:
            payload["audit"] = audit
        if fidelity:
            payload["fidelity"] = fidelity
        if verify:
            payload["verify"] = verify
        json.dump(payload, f, indent=2)
    with open(f"{artifacts_dir}/diff.patch", "w") as f:
        f.write(diff)


def _validate_confirmed_deletions(
    structural_ir: StructuralIR,
    confirmed_deletions: list[str] | None,
) -> dict | None:
    """Phase 5B: Validate that all pending deletions are confirmed.

    When confirmed_deletions is None (direct apply_engine call without API),
    auto-confirm all pending deletions (backward compat with tests).
    The API layer (/agent/apply) always passes confirmed_deletions.

    Returns None if OK, or a result dict if rejected.
    """
    pending = structural_ir.pending_deletions
    if pending and confirmed_deletions is not None:
        confirmed = set(confirmed_deletions)
        for pd in pending:
            if pd.capability not in confirmed:
                return {
                    "execution": {
                        "status": "rejected",
                        "reason": f"unconfirmed_deletion:{pd.capability}",
                        "detail": f"Pending deletion '{pd.capability}' must be confirmed via confirmed_deletions. "
                                 f"Confirmed: {confirmed}",
                        "diff": None, "operations": [],
                    },
                    "context": {"repo_snapshot": []},
                }
    return None


def _validate_delete_authority(structural_ir: StructuralIR, plan: dict) -> None:
    """Gate: every StructuralIR DELETE must be backed by a user-confirmed 'remove' action.

    Raises ValueError if an unconfirmed DELETE is found.
    """
    delete_ops = [
        op for op in structural_ir.operations
        if op.get("action") == "DELETE"
    ]
    if not delete_ops:
        return

    user_remove_actions = [
        a for a in (plan.get("actions") or [])
        if isinstance(a, dict) and a.get("verb") == "remove"
    ]
    if not user_remove_actions:
        raise ValueError(
            f"DELETE operations found ({len(delete_ops)}) but no user-confirmed "
            f"'remove' action in plan. "
            f"Delete targets: {[op.get('target') for op in delete_ops]}"
        )

    remove_targets = set()
    for a in user_remove_actions:
        t = a.get("target_capability", "")
        if t:
            remove_targets.add(t)
        hint = a.get("instance_hint", "")
        if hint:
            remove_targets.add(hint)

    for op in delete_ops:
        target = op.get("target", "")
        if target not in remove_targets:
            raise ValueError(
                f"DELETE operation for '{target}' has no matching user-confirmed "
                f"'remove' action. Confirmed remove targets: {remove_targets}"
            )


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


def _build_name_map() -> dict[str, str]:
    """Build reverse lookup: filename pattern → capability name.

    Dos fuentes:
      1. STRUCTURAL_SCHEMA suffixes (PascalCase, no-underscore, raw)
      2. FILENAME_ALIASES (mapeos explícitos para archivos reales
         cuyos nombres no siguen el patrón del schema, ej: LineChart)
    """
    from app.engine.structural_completion import STRUCTURAL_SCHEMA
    from app.engine.aliases import FILENAME_ALIASES
    name_map: dict[str, str] = {}
    for cap_name in STRUCTURAL_SCHEMA:
        suffix = cap_name.rsplit(".", 1)[-1]
        parts = suffix.split("_")
        pascal = "".join(p.title() for p in parts)
        name_map[pascal.lower()] = cap_name
        name_map[suffix.replace("_", "").lower()] = cap_name
        name_map[suffix.lower()] = cap_name
    for alias, cap_name in FILENAME_ALIASES.items():
        name_map[alias.lower()] = cap_name
    return name_map


def _build_normalized_map(name_map: dict[str, str]) -> dict[str, str]:
    """Precompute normalized (strip _, -) reverse lookup from name_map."""
    normalized: dict[str, str] = {}
    for pattern, cap_name in name_map.items():
        key = pattern.replace("_", "").replace("-", "")
        if key not in normalized:
            normalized[key] = cap_name
    return normalized


@lru_cache(maxsize=1)
def _get_normalized_map() -> dict[str, str]:
    """Cached normalized map for performance (O(1) per file)."""
    return _build_normalized_map(_build_name_map())


def _match_file_to_capability(name_lower: str, name_map: dict[str, str]) -> str | None:
    """Match filename (lowercased, no ext) to capability name.

    Two levels:
      1. exact match
      2. normalized exact match (strip _, -)

    NO substring matching. NO NLP.
    """
    if name_lower in name_map:
        return name_map[name_lower]
    normalized = name_lower.replace("_", "").replace("-", "")
    if normalized in _get_normalized_map():
        return _get_normalized_map()[normalized]
    return None


def _stem_matches_hint(fp: str, hint_stem: str) -> bool:
    """Exact filename stem match (no substring).

    hint_stem is already lowercased and .tsx-stripped.
    """
    stem = os.path.splitext(os.path.basename(fp))[0].lower()
    return stem == hint_stem or stem.replace("-", "").replace("_", "") == hint_stem


def _render_minimal_component(name: str, schema: dict) -> str:
    """Render a minimal React component skeleton."""
    import json
    stub_props = {}
    for req in schema.get("required", []):
        stub_props[req] = "null"
    props_str = json.dumps(stub_props) if stub_props else "{}"
    return (
        f"import React from 'react';\n\n"
        f"interface {name}Props {props_str.replace('null', 'any')}\n\n"
        f"const {name}: React.FC<{name}Props> = (props) => {{\n"
        f"  return <div>{name} component</div>;\n"
        f"}};\n\n"
        f"export default {name};\n"
    )


def _reconcile_substitution_targets(
    substitution_ops: tuple[SubstitutionOp, ...],
    structural_index,
    workspace: str,
    contract,
) -> list[FileOp]:
    """Check each substitution target against StructuralIndex.

    Phase 5A: uses structural_index.exists(target) — NOT os.path.exists().
    Only produces FileOp(create) if target does NOT exist in the index.
    NO lifecycle mutation — does not read or write StructuralIR.capabilities.

    Returns list of FileOp(action="create", pipeline_route="substitution").
    """
    from app.engine.structural_completion import STRUCTURAL_SCHEMA

    name_map = _build_name_map()
    fileops: list[FileOp] = []

    for op in substitution_ops:
        if structural_index.exists(op.target):
            continue

        # Target does not exist — find contract file path
        target_path = None
        for suffix, cap in name_map.items():
            if cap == op.target:
                component_name = extract_component_name(suffix)
                break
        else:
            component_name = op.target.rsplit(".", 1)[-1]
            component_name = "".join(p.title() for p in component_name.split("_"))

        contract_file_entries = (contract.renderer or {}).get("files", [])
        for entry in contract_file_entries:
            fname = os.path.basename(entry.get("path", ""))
            name_part = os.path.splitext(fname)[0].lower()
            if component_name.lower().startswith(name_part) or name_part in component_name.lower():
                target_path = entry["path"]
                break

        if not target_path:
            continue

        schema = STRUCTURAL_SCHEMA.get(op.target, {})
        template = _render_minimal_component(component_name, schema)
        fileops.append(FileOp(
            action="create",
            path=target_path,
            content=template,
            pipeline_route="substitution",
            metadata={"target_capability": op.target},
        ))

    return fileops


def _redirect_imports(
    old_capability: str,
    new_capability: str,
    workspace: str,
    contract,
) -> list[FileOp]:
    """Redirect imports from old_capability to new_capability across all files.

    Phase 4+5A: returns list of FileOp(modify) instead of writing directly.
    Phase 4: import redirection — NO filesystem delete, NO lifecycle mutation.
    Phase 5A: all writes go through FileOpApplier.
    """
    name_map = _build_name_map()
    old_component = None
    new_component = None
    for suffix, cap in name_map.items():
        if cap == old_capability and old_component is None:
            old_component = extract_component_name(suffix)
        if cap == new_capability and new_component is None:
            new_component = extract_component_name(suffix)

    if not old_component or not new_component:
        return []

    fileops: list[FileOp] = []
    for root, _dirs, files in os.walk(workspace):
        for fname in files:
            if not fname.endswith((".tsx", ".ts", ".jsx", ".js")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath) as f:
                    content = f.read()
            except Exception:
                continue

            # Skip binary or huge files
            if len(content) > 50000:
                continue

            # Check if this file imports the old component
            orig = content
            # Pattern: `from './OldComponent'` or `from './path/OldComponent'`
            import_patterns = [
                f"from './{old_component}'",
                f'from "./{old_component}"',
                f"from './{old_component.lower()}'",
                f'from "./{old_component.lower()}"',
            ]
            changes = False
            for pattern in import_patterns:
                replacement = pattern.replace(old_component, new_component)
                if pattern in content:
                    content = content.replace(pattern, replacement)
                    changes = True

            if changes:
                rel_path = os.path.relpath(fpath, workspace)
                fileops.append(FileOp(
                    action="modify",
                    path=rel_path,
                    content=content,
                    pipeline_route="substitution",
                    metadata={"old_capability": old_capability, "new_capability": new_capability},
                ))
    return fileops


def build_substitution_fileops(
    substitution_ops: tuple[SubstitutionOp, ...],
    structural_index,
    workspace: str,
    contract,
) -> tuple[list[FileOp], list[RefactorChange]]:
    """Coordina reconciliation + import redirect para substitution_ops.

    Phase 5A: execution planner layer.
    Phase 5C: returns refactor_changes alongside fileops.
    Semantic stream (SubstitutionOp) → FileOps.
    NO muta StructuralIR. NO escribe directo a disco.

    Returns (list[FileOp], list[RefactorChange]) con pipeline_route="substitution".
    """
    fileops: list[FileOp] = []
    refactor_changes: list[RefactorChange] = []

    # 1. Reconcile targets: create files if missing
    create_ops = _reconcile_substitution_targets(
        substitution_ops, structural_index, workspace, contract,
    )
    for op in create_ops:
        fileops.append(op)
        refactor_changes.append(RefactorChange(
            change_type="import_redirect",
            source=op.metadata.get("source_capability", ""),
            target=op.metadata.get("target_capability", ""),
            file_path=op.path,
            reason=f"create target file for substitution: {op.path}",
        ))

    # 2. Redirect imports: modify files with updated import paths
    for op in substitution_ops:
        redirect_ops = _redirect_imports(
            op.source, op.target, workspace, contract,
        )
        for fop in redirect_ops:
            fileops.append(fop)
            refactor_changes.append(RefactorChange(
                change_type="import_redirect",
                source=op.source,
                target=op.target,
                file_path=fop.path,
                reason=f"redirect imports {op.source} → {op.target} in {fop.path}",
            ))

    return fileops, refactor_changes


def apply_substitutions(
    structural_ir: StructuralIR,
    workspace: str,
    contract,
    structural_index=None,
    dry_run: bool = False,
) -> tuple[list[FileOp], dict, list[RefactorChange]]:
    """Phase 4+5A+5C: execute substitution_ops via build_substitution_fileops().

    Phase 4: semantic stream (SubstitutionOp) — no lifecycle effect.
    Phase 5A: all writes go through FileOp pipeline, not direct disk.
    Phase 5C: returns refactor_changes for execution trace.

    Returns (fileops, summary_dict, refactor_changes).
    """
    subs = structural_ir.substitution_ops
    if not subs:
        return [], {"created": [], "redirected": [], "warnings": []}, []

    if structural_index is None:
        from app.engine.structural_index import StructuralIndex
        structural_index = StructuralIndex.from_worktree(workspace)

    fileops, refactor_changes = build_substitution_fileops(subs, structural_index, workspace, contract)

    summary: dict[str, list] = {"created": [], "redirected": [], "warnings": []}
    for fop in fileops:
        if fop.action == "create":
            summary["created"].append(fop.path)
        elif fop.action == "modify":
            summary["redirected"].append(fop.path)

    return fileops, summary, refactor_changes


def _build_audit(
    graph, fileops, exec_ctx, contract,
    skill_ir_obj, config,
    decisions=None, identities=None, resolved_mapping=None,
    file_nodes=None, workspace_root="",
    render_ctx=None, emit_log=None,
    structural_ir=None, sreport=None,
    semantic_resolution=None, contract_resolution=None,
) -> dict:
    """Build run artifact audit with layered trace (semantic, contract, structural, ui, execution)."""
    anomalies: list[dict] = []

    # ── Section 1: intent/semantic layer ──
    requested_intents: list[dict] = []

    if structural_ir is not None:
        from app.engine.structural_completion import CompletionMode
        for rc in structural_ir.capabilities:
            if rc.mode != CompletionMode.SAFE_SKIP:
                requested_intents.append({
                    "type": rc.name,
                    "params": dict(rc.params),
                    "mode": rc.mode.value if hasattr(rc.mode, 'value') else str(rc.mode),
                })

    bound_nodes: dict[str, dict] = {}
    for node_id, node in graph.nodes.items():
        if hasattr(node, 'data') and node.data:
            bound_nodes[node_id] = {
                "type": getattr(node, 'type', 'unknown'),
                "data": dict(node.data),
                "component_instance_path": node.component_instance_path,
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



    if structural_ir is not None:
        # NEW PATH: layered trace — each layer reports only its own authority
        trace: dict = {}

        # Semantic layer: pure language interpretation, no contract knowledge
        if semantic_resolution is not None:
            trace["semantic"] = {
                "params": semantic_resolution.semantic_params,
                "provenance": semantic_resolution.semantic_provenance,
                "confidence": semantic_resolution.confidence,
                "resolution_trace": semantic_resolution.resolution_trace,
            }

        # Contract layer: SkillIR proposal adapted to contract schema
        if contract_resolution is not None:
            trace["contract"] = {
                "contract_id": contract_resolution.contract_id,
                "contract_version": contract_resolution.contract_version,
                "params": contract_resolution.contract_params,
                "provenance": contract_resolution.contract_provenance,
                "confidence": contract_resolution.confidence,
                "errors": contract_resolution.resolution_errors,
            }

        # Structural layer: capabilities resolved with definitive ownership
        trace["structural"] = {
            "contract_id": structural_ir.contract_id,
            "contract_version": structural_ir.contract_version,
            "capabilities": [
                {
                    "name": rc.name,
                    "params": dict(rc.params),
                    "mode": rc.mode.value if hasattr(rc.mode, 'value') else str(rc.mode),
                    "provenance": rc.provenance,
                }
                for rc in structural_ir.capabilities
            ],
            "coverage": {
                "completeness": sreport.completeness if sreport else 1.0,
                "safe_skip_count": sreport.safe_skip_count if sreport else 0,
                "warnings": sreport.warnings if sreport else [],
            },
            "completion_warnings": structural_ir.completion_warnings,
        }

        # UI layer: rendering representation + semantic binding fidelity
        _last_tree = ReactBackend.last_ui_tree
        if _last_tree is not None:
            ui_meta = {
                "semantic_warnings": list(_last_tree.semantic_warnings),
                "semantic_fidelity_score": _last_tree.semantic_fidelity_score,
                "consumed_params": sorted(_last_tree.consumed_params),
            }
        trace["ui"] = {
            "graph": {
                "nodes": list(graph.nodes.keys()),
                "edges": len(graph.edges),
            },
            "render_coverage": render_coverage,
            "semantic": ui_meta,
        }

        # Execution layer: binding and side effects
        trace["execution"] = {
            "bound": bound_nodes,
            "semantic_loss": semantic_loss,
            "rendered": list(emit_log) if emit_log else [],
        }

        output = {
            "trace": trace,
            "component_identity": component_identity,
            "pipeline_route": pipeline_route,
            "ownership": ownership,
            "repo_integrity": repo_integrity,
            "anomalies": anomalies,
        }
        return output


def apply_engine(run_id, plan: dict, context, dry_run: bool = False, compiler_mode: str = "strict", confirmed_deletions: list[str] | None = None):
    """Execute a plan against a workspace using the StructuralIR pipeline.

    Pipeline:
      1. SemanticResolution from frame
      2. ContractResolution from SkillIR
      3. StructuralIR (merge + slot mapping)
      4. GraphIR pipeline (build_from_structural + layout + validate)
      5. ConstraintGraph (if enabled) or BackendRenderer → FileOps
      6. Apply → git commit
    """
    guard_within(context.workspace, settings.RUNS_DIR)
    guard_within(context.artifacts, settings.ARTIFACTS_DIR)
    os.makedirs(context.artifacts, exist_ok=True)

    # ── Gate guard: must have confirmed_intent and gate not blocked ──
    plan_gate = plan.get("gate") if isinstance(plan, dict) else {}
    if isinstance(plan_gate, dict) and plan_gate.get("blocked", False):
        return {
            "execution": {"status": "rejected", "reason": "gate_blocked", "diff": None, "operations": []},
            "context": {"repo_snapshot": []},
        }

    if isinstance(plan, dict) and "confirmed_intent" not in plan and "intents" not in plan.get("semantic_frame", {}):
        # Allow plans that come from CompiledPlan (have intents)
        pass

    # ── Preflight: semantic_frame must exist and have actions ──
    if not isinstance(plan, dict) or "semantic_frame" not in plan:
        return {
            "execution": {"status": "rejected", "reason": "no_semantic_frame", "diff": None, "operations": []},
            "context": {"repo_snapshot": []},
        }

    # ── Build ExecutionContext ──
    exec_ctx = ExecutionContext(
        run_id=run_id,
        workspace_root=context.workspace,
        worktree_id=f"agent-{run_id[:8]}",
        artifacts_dir=context.artifacts,
        active_route="constraint" if FEATURE_FLAGS.get("constraint_graph", False) else "renderer",
    )

    skill_ir = plan.get("skill_ir")
    if not skill_ir:
        return {
            "execution": {"status": "rejected", "reason": "no_skill_ir", "diff": None, "operations": []},
            "context": {"repo_snapshot": []},
        }

    skill_ir_obj = SkillIR.from_dict(skill_ir)
    contract = get_contract(skill_ir_obj.contract_id, skill_ir_obj.version)
    if contract is None:
        return {
            "execution": {"status": "rejected", "reason": f"contract_not_found:{skill_ir_obj.contract_id}", "diff": None, "operations": []},
            "context": {"repo_snapshot": []},
        }

    # ── Step 1: Build StructuralIR (SemanticResolution + ContractResolution) ──
    structural_ir: StructuralIR | None = None
    sreport: StructuralCoverageReport | None = None
    semantic_resolution: SemanticResolution | None = None
    contract_resolution: ContractResolution | None = None

    try:
        from app.engine.reconciliation import reconcile
        from app.contracts.semantic_resolution import SemanticConflictError

        semantic_frame = plan["semantic_frame"]

        # Phase 1: Pure semantic from language
        semantic_resolution = reconcile(semantic_frame, skill_ir_obj)

        # Phase 2: Contract adaptation from SkillIR proposal
        contract_resolution = ContractResolution.from_skillir(skill_ir_obj, contract)

        # Phase 3: StructuralIR (intent-only lifecycle, no repo state)
        structural_index = StructuralIndex.from_worktree(context.workspace)
        structural_ir = complete_structure(
            semantic_resolution, contract_resolution, contract, semantic_frame,
        )

        # ── Gate: Delete authority — only user-confirmed IntentAction DELETE is valid ──
        try:
            _validate_delete_authority(structural_ir, plan)
        except ValueError as e:
            return FallbackExecutionRequest(
                reason=str(e), conflict_type="delete_authority", level=2,
            ).to_result()

        # ── Early exit: all capabilities resolved to KEEP ──
        if structural_ir.has_resolved_keep_state:
            logger.info(
                "All capabilities resolved to KEEP — no changes needed "
                "(contract=%s, capabilities=%d)",
                structural_ir.contract_id, len(structural_ir.capabilities),
            )
            return {
                "execution": {"status": "ok", "diff": None, "operations": []},
                "context": {"repo_snapshot": []},
                "meta": {},
            }

        # Gate 2: StructuralCoverageValidator
        sreport = StructuralCoverageValidator.validate(structural_ir, contract)
    except (ValueError, SemanticConflictError, StructuralIntegrityError, AmbiguousStructuralTargetError) as e:
        if isinstance(e, AmbiguousStructuralTargetError):
            return FallbackExecutionRequest(
                reason=str(e), conflict_type="ambiguity", level=2,
            ).to_result()
        return {
            "execution": {"status": "rejected", "reason": f"structural:{e}", "diff": None, "operations": []},
            "context": {"repo_snapshot": []},
        }

    # ── Gate 2.5: Canonicalizer + Structural Resolver + Ambiguity Gate ──
    resolution: StructuralResolution | None = None

    if FEATURE_FLAGS.get("structural_resolver", False):
        from app.graphir.structure.registry import StructuralRegistry
        from app.graphir.structure.canonicalizer import canonicalize
        from app.graphir.structure.resolver import resolve

        try:
            registry = StructuralRegistry.build_from_contract(contract)

            # Canonicalizer: pure classifier (no filtering, no registry)
            canon_trace = canonicalize(structural_ir.operations)
            if canon_trace.structural_unknown or canon_trace.non_structural:
                logger.info(
                    "Canonicalizer: %d valid, %d unknown, %d non_structural",
                    len(canon_trace.structural_valid),
                    len(canon_trace.structural_unknown),
                    len(canon_trace.non_structural),
                )

            # No CREATE/MODIFY ops → skip resolver, proceed with fallback
            if not canon_trace.has_structural():
                logger.info("No structural intents — proceeding without resolver")
                resolution = None
                # fall through to builder (resolution=None is current default)
            else:
                resolution = resolve(structural_ir, registry, structural_index=structural_index)
                logger.info(
                    "StructuralResolver: confidence=%.2f reason=%s",
                    resolution.confidence, resolution.reason,
                )

                if resolution.reason is not None:
                    return {
                        "execution": {
                            "status": "clarification_needed",
                            "reason": resolution.reason,
                            "detail": (
                                f"No structural targets resolved. "
                                f"Canonicalization: {len(canon_trace.structural_valid)} valid, "
                                f"{len(canon_trace.structural_unknown)} unknown, "
                                f"{len(canon_trace.non_structural)} non_structural"
                            ),
                            "diff": None,
                            "operations": [],
                        },
                        "context": {
                            "repo_snapshot": [],
                            "canonicalization_trace": canon_trace.to_dict(),
                        },
                    }
        except AmbiguousStructuralTargetError as e:
            return FallbackExecutionRequest(
                reason=str(e), conflict_type="ambiguity", level=2,
                details={"canonicalization_trace": canon_trace.to_dict() if canon_trace else {}},
            ).to_result()

    # ── Step 2: GraphIR pipeline (build_from_structural + layout + validate) ──
    try:
        graph, graph_layout = GraphIRPipeline.run_from_structural(
            structural_ir,
            resolution=resolution,
        )
    except AmbiguousStructuralTargetError as e:
        return FallbackExecutionRequest(
            reason=str(e), conflict_type="ambiguity", level=2,
        ).to_result()
    except ValueError as e:
        return {
            "execution": {"status": "rejected", "reason": f"graphir:{e}", "diff": None, "operations": []},
            "context": {"repo_snapshot": []},
        }

    # ── Step 2a: LayoutResolver — aplica layout_hints de MOVE/REPLACE ──
    from app.graphir.structure.layout_resolver import resolve_layout
    graph = resolve_layout(structural_ir, graph)

    # ── Step 2b: GraphIR Purity Check ──
    try:
        enforce_graph_purity(graph)
    except Exception as e:
        return {
            "execution": {"status": "rejected", "reason": f"purity_violation:{e}", "diff": None, "operations": []},
            "context": {"repo_snapshot": []},
        }

    # ── Datasource bootstrap: ensure types/hook exist before component gen ──
    if not dry_run:
        try:
            from app.datasource.contract import DatasourceContract
            from app.graphir.backends.react_backend import generate_datasource_artifacts
            ds_contract = DatasourceContract.load(context.workspace)
            if ds_contract is not None:
                # Bootstrap: ensure types/hook exist
                # Check hook file — it's the stronger signal. If hook exists,
                # the workspace already has datasource infrastructure.
                # Generating types alone would cause KpiItem/Point type collisions.
                hook_path_bs = os.path.join(context.workspace, "frontend", "src", "hooks", "useDashboardData.ts")
                if not os.path.exists(hook_path_bs):
                    infra_ops = generate_datasource_artifacts(ds_contract, context.workspace)
                    infra_applier = FileOpApplier(context.workspace)
                    infra_applier.apply(infra_ops)
                    logger.info(
                        "DATASOURCE_BOOTSTRAP: wrote %d infrastructure files",
                        len(infra_ops),
                    )

                # Validate: check all data_access.json slices reference real fields
                try:
                    from app.signature.prop_mapper import load_page_data_source
                    page_ds = load_page_data_source()
                    if page_ds and page_ds.slices:
                        slice_warnings = ds_contract.validate_slices(list(page_ds.slices))
                        for w in slice_warnings:
                            logger.warning("DATASOURCE_SLICE_MISMATCH: %s", w)
                        if not slice_warnings:
                            logger.debug(
                                "DATASOURCE_SLICE_CHECK: all %d slices valid",
                                len(page_ds.slices),
                            )
                except Exception as inner_e:
                    logger.warning("DATASOURCE_SLICE_CHECK failed: %s", inner_e)
        except Exception as e:
            logger.warning("DATASOURCE_BOOTSTRAP failed: %s", e)

    # ── Step 3: ConstraintGraph or BackendRenderer ──
    files = contract.renderer.get("files", [])
    base_path = contract.renderer.get("base_path", "")
    path_map = {}
    for f in files:
        comp = extract_component_name(f["path"])
        if comp:
            path_map[comp] = f["path"]

    # Build file_path_overrides from structural_index for existing capabilities.
    # This ensures MODIFY operations go to the correct filesystem paths even when
    # the contract's renderer paths don't match the actual repo structure
    # (e.g., SalesOverviewPage.tsx instead of Page.tsx, or frontend/ prefix).
    capabilities_map = contract.ast_template.get("capabilities", {})
    file_path_overrides: dict[str, str] = {}
    for comp_type, cap_id in capabilities_map.items():
        file_paths = structural_index.resolve_all_file_paths(cap_id) if structural_index else []
        if file_paths:
            file_path_overrides[comp_type] = file_paths[0]

    # Phase 4.5: extract component signatures from worktree
    component_signatures: dict[str, dict] = {}
    try:
        if context and context.workspace:
            from app.signature.extractor import extract_signatures
            component_signatures = extract_signatures(context.workspace)
            logger.info("Component signatures: %d extracted", len(component_signatures))
    except Exception as e:
        logger.warning("Could not extract component signatures: %s", e)

    backend_config = BackendConfig(
        output_base_path=base_path,
        path_map=path_map,
        file_path_overrides=file_path_overrides,
        component_signatures=component_signatures,
        workspace=context.workspace,
    )

    # ── Audit var holders ──
    _audit_decisions: dict | None = None
    _audit_identities: dict | None = None
    _audit_resolved_mapping: dict | None = None
    _audit_file_nodes: dict | None = None
    _audit_render_ctx = None

    if FEATURE_FLAGS.get("constraint_graph", False):
        # ConstraintGraph pipeline
        from app.graphir.constraint.indexer import RepositoryIndexer
        from app.graphir.constraint.matcher import IntentFileMatcher
        from app.graphir.constraint.resolver import IdentityResolver
        from app.graphir.constraint.renderer import RepositoryAwareRenderer
        from app.graphir.constraint.memory import RepositorySemanticMemory
        from app.graphir.constraint.crl import ConflictResolutionLayer
        from app.graphir.constraint.split_analyzer import SPLITAnalyzer
        from app.graphir.constraint.models import MemoryRecord, Decision
        from app.graphir.constraint.context import PipelineState, RenderContext

        indexer = RepositoryIndexer()
        file_nodes, component_nodes = indexer.index(exec_ctx.workspace_root)

        # Phase 2: Load persistent semantic memory → feed resolver
        memory = RepositorySemanticMemory(exec_ctx.memory_path)
        raw_memory = memory.load()

        # Phase 3: Reconcile memory against current file state
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
        resolver = IdentityResolver(
            resolved_mapping=resolved_mapping,
            file_path_overrides=file_path_overrides,
        )
        renderer = RepositoryAwareRenderer()

        # Pre-compute decisions (needed for memory persistence)
        identities, candidates = matcher.match(graph, file_nodes)
        decisions = resolver.resolve(identities, candidates, file_nodes)

        # Phase 4: Structural analysis — detect overloaded files
        split_analyzer = SPLITAnalyzer()
        split_plan = split_analyzer.analyze(
            decisions, identities, file_nodes, crl_conflicts,
        )

        # ── Fix 4: Project render_mode from semantic decision ──
        for dec in decisions.values():
            if dec.decision in (Decision.CREATE, Decision.SPLIT):
                dec.render_mode = "create"
            elif dec.decision in (Decision.UPDATE, Decision.EXTEND):
                dec.render_mode = "modify"

        pipeline_state = PipelineState(
            file_nodes=file_nodes,
            component_nodes=component_nodes,
            decisions=decisions,
            split_plan=split_plan,
            resolved_mapping=resolved_mapping,
            exec_ctx=exec_ctx,
        )
        render_ctx = RenderContext(
            execution=pipeline_state,
            feature_flags=dict(FEATURE_FLAGS),
        )

        ReactBackend.reset_emit_log(run_id)
        ReactBackend.reset_traces(run_id)

        # Build content snapshot for pure renderer (A2)
        existing_content: dict[str, str] = {}
        for dec in decisions.values():
            if dec.decision in (Decision.UPDATE, Decision.EXTEND):
                target = dec.target_file
                if target and target not in existing_content:
                    abs_path = os.path.join(exec_ctx.workspace_root, target)
                    if os.path.exists(abs_path):
                        with open(abs_path) as f:
                            existing_content[target] = f.read()

        contract_params = contract_resolution.contract_params if contract_resolution else None
        resolved_bindings = resolve_bindings(
            contract_params or {},
        ) if contract_params else None
        try:
            fileops = renderer.render(
                graph, graph_layout, backend_config,
                context=render_ctx,
                existing_content_by_path=existing_content,
                resolved_bindings=resolved_bindings,
            )
        except MISSING_REQUIRED_PROPS as e:
            return FallbackExecutionRequest(
                reason=str(e), conflict_type="missing_required_param", level=2,
            ).to_result()

        # Collect constraint vars for audit
        _audit_decisions = decisions
        _audit_identities = identities
        _audit_resolved_mapping = resolved_mapping
        _audit_file_nodes = file_nodes
        _audit_render_ctx = render_ctx

        # Phase 2: Persist new identity→file mappings
        updated = memory.merge(
            decisions, identities, resolved_mapping,
        )
        memory.save(updated)
    else:
        # Direct BackendRenderer
        ReactBackend.reset_emit_log(run_id)
        ReactBackend.reset_traces(run_id)
        backend = ReactBackend()
        contract_params = contract_resolution.contract_params if contract_resolution else None
        resolved_bindings = resolve_bindings(
            contract_params or {},
        ) if contract_params else None
        try:
            fileops = backend.render(graph, graph_layout, backend_config, resolved_bindings=resolved_bindings)
        except MISSING_REQUIRED_PROPS as e:
            return FallbackExecutionRequest(
                reason=str(e), conflict_type="missing_required_param", level=2,
            ).to_result()

    # ── Capture emitted props ──
    _captured_emit_log = list(ReactBackend._emit_log.get(run_id, []))

    # ── Phase 5B: Validate confirmed_deletions covers all pending deletions ──
    confirmed_result = _validate_confirmed_deletions(structural_ir, confirmed_deletions)
    if confirmed_result is not None:
        return confirmed_result

    # ── Inject DELETE FileOps for structural DELETE operations ──
    # MUST follow DELETE RESOLUTION CONTRACT v1
    # (backend/app/engine/delete_resolution_contract.py)
    delete_targets = [
        op for op in structural_ir.operations
        if op.get("action") == "DELETE"
    ]
    try:
        if delete_targets:
            for op in delete_targets:
                target = op["target"]
                hint = op.get("instance_hint")
                all_files = structural_index.resolve_all_file_paths(target) if structural_index else []
                if len(all_files) == 0:
                    logger.warning("DELETE target '%s' has no files in workspace", target)
                    continue
                if len(all_files) == 1:
                    fileops.append(FileOp(action="delete", path=all_files[0], content="", pipeline_route="delete_inject"))
                    continue
                # Multiple files: must resolve uniquely or raise
                names = [os.path.basename(fp) for fp in all_files]
                if hint:
                    hint_stem = hint.lower().replace(".tsx", "")
                    matched = [fp for fp in all_files if _stem_matches_hint(fp, hint_stem)]
                    if len(matched) == 1:
                        fileops.append(FileOp(action="delete", path=matched[0], content="", pipeline_route="delete_inject"))
                        continue
                    elif len(matched) == 0:
                        raise AmbiguousStructuralTargetError(
                            f"No file matches '{hint}'. "
                            f"Available files: {', '.join(names)}. "
                            f"Try being more specific (e.g., \"remove the {names[0].replace('.tsx', '').lower()}\")."
                        )
                    else:
                        raise AmbiguousStructuralTargetError(
                            f"'{hint}' matches multiple files: {', '.join(os.path.basename(fp) for fp in matched)}. "
                            f"Please be more specific."
                        )
                # No hint: cannot delete all — ambiguous
                raise AmbiguousStructuralTargetError(
                    f"Multiple files to choose from: {', '.join(names)}. "
                    f"Please specify which one to remove "
                    f"(e.g., \"remove the {names[0].replace('.tsx', '').lower()}\")."
                )
    except AmbiguousStructuralTargetError as e:
        return {
            "execution": {
                "status": "clarification_needed", "reason": "ambiguous_target",
                "detail": str(e), "diff": None, "operations": [],
            },
            "context": {"repo_snapshot": []},
        }

    # ── Phase 5A+5C: Build substitution FileOps and add to pipeline ──
    sub_fileops: list[FileOp] = []
    sub_summary: dict = {"created": [], "redirected": [], "warnings": []}
    sub_refactor_changes: list[RefactorChange] = []
    if structural_ir.substitution_ops:
        sub_fileops, sub_summary, sub_refactor_changes = apply_substitutions(
            structural_ir, context.workspace, contract,
            structural_index=structural_index, dry_run=dry_run,
        )
        fileops.extend(sub_fileops)
        if sub_summary["created"] or sub_summary["redirected"]:
            logger.info(
                "substitution_ops: created=%d redirected=%d",
                len(sub_summary["created"]), len(sub_summary["redirected"]),
            )

    # ── Validate substitution ops consistency (post-hoc, no lifecycle effect) ──
    from app.engine.structural_completion import _validate_substitution_ops_consistency
    sub_warnings = _validate_substitution_ops_consistency(
        structural_ir, fileops, structural_index=structural_index,
    )
    for w in sub_warnings:
        logger.warning("substitution_consistency: %s", w)

    # ── Collision detection ──
    from app.graphir.utils import validate_fileop_collisions
    collisions = validate_fileop_collisions(fileops)
    if collisions:
        return FallbackExecutionRequest(
            reason=f"FileOp collisions detected: {len(collisions)}",
            conflict_type="collision", level=2,
            details={"collisions": collisions},
        ).to_result()

    ok, vreason = validate_fileops(fileops)
    if not ok:
        return {
            "execution": {"status": "rejected", "reason": vreason, "diff": None, "operations": []},
            "context": {"repo_snapshot": []},
        }

    applier = FileOpApplier(context.workspace)
    results = applier.apply(fileops)

    logger.info(
        "apply: contract_id=%s version=%d params=%s fileops_count=%d",
        skill_ir_obj.contract_id, skill_ir_obj.version, skill_ir_obj.params, len(fileops),
    )

    # ── Fase 4: Verify BEFORE git commit ────────────────────────────
    verify_result = None
    if not dry_run and FEATURE_FLAGS.get("verify_worktree", True):
        try:
            from app.engine.verify_worktree import verify_worktree
            verify_result = verify_worktree(context.workspace)
        except Exception as e:
            logger.warning("[verify] verify_worktree failed: %s", e)
            verify_result = {"status": "error", "check": "none", "errors": [str(e)], "output": ""}

    # Git flow — skip commit if verify failed, still capture diff
    execution_status = "ok"
    if verify_result and verify_result.get("status") in ("failed", "error"):
        execution_status = "verify_failed"
        subprocess.run(["git", "add", "-A"], cwd=context.workspace, check=False)
        diff = generate_diff(context.workspace)
        logger.warning("[apply] verify failed — skipping git commit, status=verify_failed")
    else:
        diff, err = _run_git_flow(context.workspace, run_id, dry_run)
        if err:
            return {
                "execution": {"status": "rejected", "reason": "git_commit_failed", "detail": err, "diff": None, "operations": []},
                "context": {"repo_snapshot": []},
            }

    # ── Step 4: Filter plan for artifacts (strip decomposition fields) ──
    clean_plan = {k: v for k, v in plan.items() if k not in ("intents", "decomposition")}

    # ── Build audit ──
    audit = _build_audit(
        graph=graph,
        structural_ir=structural_ir,
        sreport=sreport,
        semantic_resolution=semantic_resolution,
        contract_resolution=contract_resolution,
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
        render_ctx=_audit_render_ctx,
        emit_log=_captured_emit_log,
    )

    # ── Step 5: Build fidelity report ──
    fidelity = {
        "completeness": sreport.completeness,
        "safe_skip_count": sreport.safe_skip_count,
        "total_capabilities": len(structural_ir.capabilities),
        "warnings": sreport.warnings,
        "execution_mode": FEATURE_FLAGS.get("execution_mode", "graphir"),
        "trace_level": FEATURE_FLAGS.get("trace_level", "full"),
    }

    _write_artifacts(
        context.artifacts, run_id, clean_plan,
        [fop.to_dict() for fop in fileops],
        results, diff or "",
        audit=audit, fidelity=fidelity, verify=verify_result,
    )

    save_run_state(run_id, {"status": "apply"})

    # ── Aggregate refactor changes from composition_sync + substitution ──
    all_refactor_changes: list[dict] = []
    for rc in structural_ir.composition_sync_trace:
        all_refactor_changes.append({
            "change_type": rc.change_type,
            "source": rc.source,
            "target": rc.target,
            "file_path": rc.file_path,
            "reason": rc.reason,
        })
    for rc in sub_refactor_changes:
        all_refactor_changes.append({
            "change_type": rc.change_type,
            "source": rc.source,
            "target": rc.target,
            "file_path": rc.file_path,
            "reason": rc.reason,
        })

    return {
        "execution": {
            "status": execution_status,
            "diff": diff or None,
            "operations": [fop.to_dict() for fop in fileops],
            "refactor_changes": all_refactor_changes,
        },
        "context": {
            "run_id": run_id,
            "dry_run": dry_run,
            "workspace": context.workspace,
            "execution_mode": FEATURE_FLAGS.get("execution_mode", "graphir"),
            "trace_level": FEATURE_FLAGS.get("trace_level", "full"),
            "repo_snapshot": [fop.path for fop in fileops],
        },
        "meta": {
            "fidelity": fidelity,
            "audit": audit,
            "verify": verify_result,
        },
    }
