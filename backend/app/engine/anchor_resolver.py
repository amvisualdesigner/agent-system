"""Anchor Resolution Layer — determines deterministically WHERE each newly
created UI component is mounted into the existing codebase.

Pipeline position:
  GraphIR → render() → CREATE ops → AnchorResolver → MODIFY ops → validate_fileops → apply

F1/C4 invariants — the result is STRICTLY PHYSICAL, never semantic:
  - No name-overlap, no domain affinities, no weighted scoring, no type
    fallback chains. The old semantic score is gone as a decision authority.
  - The anchor is derived from the contract composition map
    ({child_capability: parent_capability}, built from the contract's own
    ast_template slots) and the parent capability's PHYSICAL instances, or
    from a plan/user-specified anchor path (forced).
  - Decision matrix (exactly, no heuristics):
      forced           → use it (the plan already specified which one)
      0 candidates     → CONFLICT
      1 candidate      → use it
      N candidates     → CONFLICT unless the plan already specified which one
  - A CREATE component whose parent is being regenerated this run is already
    composed by the renderer (import present in the anchor content) → skipped
    as composed, never double-mounted.
  - No second independent GraphIR → FileOps route: the resolver consumes the
    same FileOps + StructuralIndex at the same pipeline position and only
    emits/merges MODIFY ops.
  - No ML, no embeddings, no external heuristics, no new graphs.
  - Does NOT modify GraphIR semantic.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from app.engine.structural_index import StructuralIndex
from app.graphir.models import FileOp
from app.graphir.utils import extract_component_name

logger = logging.getLogger(__name__)

NON_MOUNTABLE_TYPES = frozenset({
    "Page",
    "DashboardLayout",
    "AppLayout",
    "RootLayout",
    "Layout",
})

LAYOUT_PAGE_CAPABILITIES = frozenset({"layout.page"})


@dataclass
class AnchorCandidate:
    """A physical mount location (existing container file)."""
    type: str  # always "feature_page" (physical page container)
    file_path: str  # path to the file to modify
    page_name: str  # human-readable name


def _find_pages_from_index(structural_index: StructuralIndex) -> list[AnchorCandidate]:
    """Find all existing page files via StructuralIndex (layout.page capability)."""
    pages: list[AnchorCandidate] = []
    for cap_id in structural_index:
        is_page_cap = cap_id in LAYOUT_PAGE_CAPABILITIES or cap_id.startswith("layout.")
        for inst in structural_index.get_instances(cap_id):
            fp = inst.file_path
            if not fp or not fp.endswith(".tsx"):
                continue
            name = os.path.splitext(os.path.basename(fp))[0]
            if is_page_cap or "Page" in name:
                pages.append(AnchorCandidate(
                    type="feature_page",
                    file_path=fp,
                    page_name=name,
                ))
    return pages


def _find_pages_from_filesystem(workspace: str) -> list[AnchorCandidate]:
    """Scan the filesystem for existing Page files not captured by StructuralIndex."""
    pages: list[AnchorCandidate] = []
    page_pattern = re.compile(r"(Page|Overview|Dashboard|Screen)$")
    try:
        for root, _dirs, files in os.walk(workspace):
            for fn in files:
                if not fn.endswith(".tsx"):
                    continue
                name = os.path.splitext(fn)[0]
                if page_pattern.search(name):
                    rel_path = os.path.relpath(os.path.join(root, fn), workspace)
                    if rel_path not in {p.file_path for p in pages}:
                        pages.append(AnchorCandidate(
                            type="feature_page",
                            file_path=rel_path,
                            page_name=name,
                        ))
    except OSError:
        pass
    return pages


def _collect_page_anchors(
    structural_index: StructuralIndex,
    workspace: str,
) -> list[AnchorCandidate]:
    """Collect the PHYSICAL set of existing page container files.

    Candidates come from the structural index (instance registry) plus a
    filesystem scan of page-named files. Layouts, routers and section shells
    are NOT candidates: an arbitrary component mounted into an infra shell is
    a routing/rendering decision, i.e. semantic, and is out of scope for a
    strictly physical anchor.
    """
    pages: list[AnchorCandidate] = []
    seen: set[str] = set()
    for pc in _find_pages_from_index(structural_index):
        if pc.file_path and pc.file_path not in seen:
            seen.add(pc.file_path)
            pages.append(pc)
    for pc in _find_pages_from_filesystem(workspace):
        if pc.file_path not in seen:
            seen.add(pc.file_path)
            pages.append(pc)
    return pages


def _is_mountable_ui_component(fileop: FileOp) -> bool:
    """Determine if a CREATE FileOp represents a visible UI component that
    needs mounting into a parent page/layout.

    Excludes:
    - Infrastructure files (types, hooks, datasource bootstrap)
    - Substitution or delete ops
    - Non-.tsx files
    - Page/layout files themselves (they are the containers, not the content)
    """
    if fileop.action != "create":
        return False
    if fileop.pipeline_route in ("infrastructure", "substitution", "delete_inject"):
        return False
    if not fileop.path.endswith(".tsx"):
        return False

    name = extract_component_name(fileop.path)
    if not name:
        return False
    if name in NON_MOUNTABLE_TYPES:
        return False
    if "Page" in name and name.endswith("Page"):
        return False
    if any(name.startswith(l) for l in ("Dashboard", "App", "Root")):
        if "Layout" in name:
            return False

    return True


def _capability_for_path(
    structural_index: StructuralIndex,
    file_path: str,
) -> str | None:
    """Reverse-lookup the capability that owns a given instance file path.

    Falls back to the deterministic path→capability name map used by the
    rest of the pipeline (provenance validation), so a CREATE component
    that is not yet registered in the index still resolves to the
    capability it is ABOUT to materialize.
    """
    for cap_id in structural_index:
        for inst in structural_index.get_instances(cap_id):
            if inst.file_path == file_path:
                return cap_id
    from app.engine.structural_completion import _capability_from_path
    return _capability_from_path(file_path)


def _content_imports(content: str, component_name: str) -> bool:
    """Physically check whether a file's content already imports a component."""
    import_pattern = re.compile(
        r"import\s+\{?\s*" + re.escape(component_name) + r"\s*\}?\s+from\s+['\"]",
    )
    return bool(import_pattern.search(content or ""))


def _inject_component_into_content(
    content: str,
    component_name: str,
    component_path: str,
    page_dir: str,
) -> str | None:
    """Inject import and JSX mount for a component into existing file content.

    Returns modified content; None if the component is already imported or if
    injection has no physically valid site (no import block / no mount point).
    This is a pure function — no filesystem access.
    """
    if not component_name:
        return None

    # Check if already imported
    import_pattern = re.compile(
        r"import\s+\{?\s*" + re.escape(component_name) + r"\s*\}?\s+from\s+['\"]",
    )
    if import_pattern.search(content):
        return None

    # Compute relative import path from page dir to component
    norm_component = component_path
    rel_import = os.path.relpath(
        os.path.splitext(norm_component)[0],
        page_dir,
    )
    if not rel_import.startswith("."):
        rel_import = "./" + rel_import

    import_line = f"import {{{component_name}}} from '{rel_import}';"

    lines = content.split("\n")
    # Find last import statement
    last_import_idx = -1
    for i, line in enumerate(lines):
        if re.match(r"\s*import\s+.*from\s+['\"]", line):
            last_import_idx = i
    if last_import_idx < 0:
        return None

    lines.insert(last_import_idx + 1, import_line)

    # Find mount point: inside return's JSX, before closing tag
    mount_tag = f"<{component_name} />"
    joined = "\n".join(lines)

    # Try various closing patterns in order of preference
    close_pos = -1
    for pattern in ["</div>", "</>", "</Fragment>", "</React.Fragment>"]:
        pos = joined.rfind(pattern)
        if pos >= 0:
            close_pos = pos
            break
    if close_pos < 0:
        return None

    # Preserve indentation
    pre_lines = [l for l in joined[:close_pos].rstrip("\n").split("\n") if l.strip()]
    last_line = pre_lines[-1] if pre_lines else ""
    indent = last_line[:len(last_line) - len(last_line.lstrip())]

    new_joined = (
        joined[:close_pos]
        + "\n" + indent + mount_tag
        + "\n" + joined[close_pos:]
    )

    return new_joined


def _get_content_for_anchor_path(
    all_fileops: list[FileOp],
    target_path: str,
    workspace: str,
) -> str | None:
    """Get anchor file content, checking pipeline-preshed content first.

    Priority:
      1. CREATE ops (anchor file being created this run)
      2. MODIFY ops (anchor file already modified this run, e.g. by renderer)
      3. Disk (existing file)
    """
    for fop in all_fileops:
        if fop.action in ("create", "modify") and fop.path == target_path:
            return fop.content
    abs_path = os.path.join(workspace, target_path)
    if not os.path.isfile(abs_path):
        return None
    try:
        with open(abs_path) as f:
            return f.read()
    except OSError:
        return None


def _normalize_forced_path(
    workspace: str,
    forced_anchor_path: str | None,
) -> str | None:
    """Resolve the plan/user-specified anchor to a relative existing path, or
    None if it does not exist (the plan did not actually specify a mountable
    anchor)."""
    if not forced_anchor_path:
        return None
    if os.path.isabs(forced_anchor_path):
        rel = os.path.relpath(forced_anchor_path, workspace)
        full = forced_anchor_path
    else:
        rel = forced_anchor_path
        full = os.path.join(workspace, forced_anchor_path)
    if not os.path.isfile(full):
        logger.warning(
            "ANCHOR_RESOLVER: forced_anchor_path %s not found on disk, ignoring",
            forced_anchor_path,
        )
        return None
    return rel


# ── Invariant: audit only, never input ─────────────────────────────────
# AnchorResolutionDecisions is produced by resolve_anchors() and flows
# exclusively into meta.audit.anchor_resolution for post-hoc diagnostics.
# It MUST NOT be read back as input to any decision — not by the anchor
# resolver itself, not by any downstream module. The decisions dict is
# a trace of what happened, not a signal for what should happen next.
AnchorResolutionDecisions = dict[str, dict]


def resolve_anchors(
    all_fileops: list[FileOp],
    structural_index: StructuralIndex,
    workspace: str,
    forced_anchor_path: str | None = None,
    composition_map: dict[str, str] | None = None,
) -> tuple[list[FileOp], list[str], AnchorResolutionDecisions, list[str]]:
    """Resolve anchors for mountable CREATE FileOps and produce MODIFY FileOps.

    The decision is STRICTLY PHYSICAL (F1/C4): forced → use; 0 candidates →
    CONFLICT; 1 candidate → use; N candidates → CONFLICT unless the plan
    specified one (forced). No semantic scoring.

    Args:
        all_fileops: All FileOps from the pipeline (both CREATE and others).
        structural_index: StructuralIndex of the workspace.
        workspace: Workspace root path.
        forced_anchor_path: If set, the plan/user explicitly chose the anchor
            (e.g. PageCreator "create_new") — use it for every mountable
            component, overriding ambiguity.
        composition_map: {child_capability: parent_capability} derived from
            the contract's ast_template (physical composition hierarchy).

    Returns:
        (modify_ops, unresolved, decisions, conflicts): MODIFY FileOps for
        anchor injection, list of unresolved component paths (kept empty for
        backwards-compat callers), the audit decisions dict, and a list of
        physical-ambiguity CONFLICT messages (0 candidates or N candidates).
        Callers must treat any conflict as a whole-run CONFLICT.
    """
    create_fileops = [fop for fop in all_fileops if fop.action == "create"]
    mountable = [fop for fop in create_fileops if _is_mountable_ui_component(fop)]

    if not mountable:
        return [], [], {}, []

    composition_map = composition_map or {}

    # Physical page containers (orphan candidates). Files being created THIS
    # run are excluded: a brand-new page composes its own children at render;
    # mounting an unrelated orphan into it is not a physical relation.
    created_paths = {fop.path for fop in all_fileops if fop.action == "create"}
    page_anchors = [
        a for a in _collect_page_anchors(structural_index, workspace)
        if a.file_path not in created_paths
    ]

    forced_path = _normalize_forced_path(workspace, forced_anchor_path)

    modify_ops: list[FileOp] = []
    unresolved: list[str] = []
    conflicts: list[str] = []
    decisions: AnchorResolutionDecisions = {}

    for fop in mountable:
        component_name = extract_component_name(fop.path)
        if not component_name:
            continue

        if forced_path is not None:
            decision = "forced"
            picked = [forced_path]
            reason = f"plan-specified anchor {forced_path}"
        else:
            cap = _capability_for_path(structural_index, fop.path)
            parent_cap = composition_map.get(cap) if cap else None
            if parent_cap is not None:
                picked = sorted({
                    inst.file_path for inst in structural_index.get_instances(parent_cap)
                    if inst.file_path
                })
                source = f"contract composition {cap}→{parent_cap}"
            else:
                picked = [a.file_path for a in page_anchors]
                source = "physical page containers"

            n = len(picked)
            if n == 0:
                conflicts.append(
                    f"{fop.path}: {source} → 0 physical anchors "
                    f"(nothing to mount into; plan must specify one)"
                )
                decisions[component_name] = _decision_record(
                    fop.path, "conflict", None,
                    [{"path": p, "type": "feature_page"} for p in picked],
                    f"{source}: 0 physical anchors",
                )
                continue
            if n > 1:
                conflicts.append(
                    f"{fop.path}: {source} → {n} physical anchors "
                    f"{sorted(picked)} — ambiguous, plan must specify one"
                )
                decisions[component_name] = _decision_record(
                    fop.path, "conflict", None,
                    [{"path": p, "type": "feature_page"} for p in sorted(picked)],
                    f"{source}: {n} physical anchors (ambiguous)",
                )
                continue

            candidate = picked[0]
            # 1 candidate: if the anchor already composes this component, the
            # renderer mounted it this run (parent regenerated) → skip.
            anchor_content = _get_content_for_anchor_path(
                all_fileops, candidate, workspace,
            )
            if anchor_content is not None and _content_imports(
                anchor_content, component_name,
            ):
                decisions[component_name] = _decision_record(
                    fop.path, "composed_skip", candidate,
                    [{"path": candidate, "type": "feature_page"}],
                    "1 physical anchor; already composed by the renderer",
                )
                continue
            decision = "single"
            reason = f"{source}: 1 physical anchor"

        anchor_path = picked[0]
        anchor = AnchorCandidate(
            type="feature_page",
            file_path=anchor_path,
            page_name=os.path.splitext(os.path.basename(anchor_path))[0],
        )

        modify_op = _generate_modify_op(
            fop, anchor, workspace, all_fileops, component_name,
        )
        if modify_op is not None:
            # ── Merge with existing CREATE/MODIFY ops (e.g. from the
            #    renderer) — never emit a collision on the same path ──
            existing_idx = next(
                (i for i, existing in enumerate(all_fileops)
                 if existing.action in ("create", "modify")
                 and existing.path == modify_op.path),
                None,
            )
            if existing_idx is not None:
                existing = all_fileops[existing_idx]
                all_fileops[existing_idx] = FileOp(
                    action=existing.action,
                    path=modify_op.path,
                    content=modify_op.content,
                    pipeline_route=existing.pipeline_route,
                    metadata={
                        **(existing.metadata or {}),
                        "anchor_type": anchor.type,
                        "anchor_page": anchor.page_name,
                        "component": component_name,
                    },
                )
            else:
                modify_ops.append(modify_op)
            decisions[component_name] = _decision_record(
                fop.path, decision, anchor_path,
                [{"path": anchor_path, "type": "feature_page"}],
                reason,
            )
        else:
            conflicts.append(
                f"{fop.path}: cannot inject anchor into {anchor_path} "
                f"(no import site / no mount point)"
            )
            decisions[component_name] = _decision_record(
                fop.path, "conflict", None,
                [{"path": anchor_path, "type": "feature_page"}],
                "1 physical anchor but injection failed (no import site / mount point)",
            )

    return modify_ops, unresolved, decisions, conflicts


def _decision_record(
    component_path: str,
    decision: str,
    selected: str | None,
    candidates: list[dict],
    reason: str,
) -> dict:
    record: dict[str, Any] = {
        "component_path": component_path,
        "decision": decision,
        "candidates": candidates,
        "reason": reason,
    }
    if selected is not None:
        record["selected"] = {
            "path": selected,
            "type": "feature_page",
        }
    return record


def _generate_modify_op(
    component_op: FileOp,
    anchor: AnchorCandidate,
    workspace: str,
    all_fileops: list[FileOp],
    component_name: str,
) -> FileOp | None:
    """Generate a single MODIFY FileOp that injects a component into an anchor.

    Uses _get_content_for_anchor_path which checks pipeline fileops (CREATE
    and MODIFY) before falling back to disk.
    """
    if not component_name:
        return None

    anchor_content = _get_content_for_anchor_path(
        all_fileops, anchor.file_path, workspace,
    )
    if anchor_content is None:
        logger.warning(
            "ANCHOR_RESOLVER: anchor file %s not found in fileops or on disk",
            anchor.file_path,
        )
        return None

    page_dir = os.path.dirname(anchor.file_path)
    new_content = _inject_component_into_content(
        anchor_content,
        component_name,
        component_op.path,
        page_dir,
    )
    if new_content is None:
        return None

    return FileOp(
        action="modify",
        path=anchor.file_path,
        content=new_content,
        pipeline_route="anchor_resolution",
        metadata={
            "anchor_type": anchor.type,
            "anchor_page": anchor.page_name,
            "component": component_name,
        },
    )