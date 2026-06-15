"""Anchor Resolution Layer — deterministically resolves WHERE each newly
created UI component is mounted into the existing codebase.

Pipeline position:
  GraphIR → render() → CREATE ops → AnchorResolver → MODIFY ops → validate_fileops → apply

Design invariants:
  - No ML, no embeddings, no external heuristics
  - Pure explicit scoring over 4 anchor types (Route > Layout > Feature Page > Section)
  - Each CREATE component gets exactly one anchor or unresolved_mount warning
  - No new graphs or global representations
  - Does NOT modify GraphIR semantic
  - Does NOT depend on data_access.json as truth source
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from app.engine.structural_index import StructuralIndex
from app.graphir.models import FileOp
from app.graphir.utils import extract_component_name

logger = logging.getLogger(__name__)

MIN_SCORE = 0.3

W_SEMANTIC = 0.4
W_STRUCTURAL = 0.3
W_UI_LOCALITY = 0.3

NON_MOUNTABLE_TYPES = frozenset({
    "Page",
    "DashboardLayout",
    "AppLayout",
    "RootLayout",
    "Layout",
})

LAYOUT_PAGE_CAPABILITIES = frozenset({"layout.page"})

# Domain keyword affinities for semantic scoring
# Maps page-context words → related component keywords
DOMAIN_AFFINITIES: dict[str, set[str]] = {
    "sales": {"kpi", "revenue", "growth", "pipeline", "deal", "forecast",
              "filter", "table", "chart", "overview", "metric"},
    "dashboard": {"kpi", "chart", "metric", "filter", "table", "analytics",
                  "overview", "summary", "timeseries", "panel", "bar",
                  "line", "grid"},
    "analytics": {"kpi", "chart", "table", "filter", "metric", "timeseries",
                  "bar", "line", "trend", "panel", "overview"},
    "overview": {"kpi", "metric", "summary", "chart", "key", "top",
                 "filter", "panel", "table"},
    "report": {"table", "chart", "filter", "export", "summary", "detail",
               "panel", "bar"},
    "marketing": {"campaign", "channel", "metric", "kpi", "chart",
                  "overview", "filter", "panel"},
    "finance": {"revenue", "expense", "profit", "kpi", "table", "chart",
                "filter", "metric"},
    "filter": {"panel", "search", "bar", "input", "select", "dropdown"},
    "panel": {"filter", "kpi", "chart", "metric", "table", "info"},
    "chart": {"bar", "line", "pie", "area", "scatter", "timeseries",
              "kpi", "metric"},
}


@dataclass
class AnchorCandidate:
    """A candidate location where a component could be mounted."""
    type: str  # "route" | "layout" | "feature_page" | "section"
    file_path: str  # path to the file to modify
    page_name: str  # human-readable name
    score: float = 0.0
    existing_components: list[str] = field(default_factory=list)


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
    """Scan the filesystem for Page files not captured by StructuralIndex."""
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


def _find_layouts(workspace: str) -> list[AnchorCandidate]:
    """Find layout files (DashboardLayout, AppLayout, etc.) via filesystem scan."""
    layouts: list[AnchorCandidate] = []
    layout_pattern = re.compile(
        r"(DashboardLayout|AppLayout|RootLayout|MainLayout|SidebarLayout)",
        re.IGNORECASE,
    )
    try:
        for root, _dirs, files in os.walk(workspace):
            for fn in files:
                if not fn.endswith((".tsx", ".tsx")):
                    continue
                if layout_pattern.match(fn):
                    rel_path = os.path.relpath(os.path.join(root, fn), workspace)
                    name = os.path.splitext(fn)[0]
                    layouts.append(AnchorCandidate(
                        type="layout",
                        file_path=rel_path,
                        page_name=name,
                    ))
    except OSError:
        pass
    return layouts


def _find_router_file(workspace: str) -> str | None:
    """Find a router file in the workspace (router.tsx, App.tsx, routes.tsx)."""
    router_names = {"router.tsx", "Router.tsx", "routes.tsx", "Routes.tsx", "App.tsx"}
    try:
        for root, _dirs, files in os.walk(workspace):
            for fn in files:
                if fn in router_names:
                    return os.path.relpath(os.path.join(root, fn), workspace)
    except OSError:
        pass
    return None


def _find_section_files(workspace: str) -> list[AnchorCandidate]:
    """Find section zone files — toolbars, filter bars, KPI rows, chart grids."""
    sections: list[AnchorCandidate] = []
    section_pattern = re.compile(
        r"(Toolbar|FilterBar|ChartGrid|ActionBar|Sidebar|Header|Footer|NavBar|ContentArea)",
        re.IGNORECASE,
    )
    try:
        for root, _dirs, files in os.walk(workspace):
            for fn in files:
                if not fn.endswith((".tsx", ".tsx")):
                    continue
                if section_pattern.match(fn):
                    rel_path = os.path.relpath(os.path.join(root, fn), workspace)
                    name = os.path.splitext(fn)[0]
                    sections.append(AnchorCandidate(
                        type="section",
                        file_path=rel_path,
                        page_name=name,
                    ))
    except OSError:
        pass
    return sections


def _extract_existing_imports(file_path: str, workspace: str) -> list[str]:
    """Extract imported component names from a file."""
    abs_path = os.path.join(workspace, file_path)
    if not os.path.isfile(abs_path):
        return []
    try:
        with open(abs_path) as f:
            content = f.read()
    except OSError:
        return []

    names: list[str] = []
    for line in content.split("\n"):
        stripped = line.strip()
        m = re.match(
            r"import\s+(\w+)",
            stripped,
        )
        if m:
            names.append(m.group(1))
            continue
        m = re.match(
            r"import\s+\{\s*(\w+)",
            stripped,
        )
        if m:
            names.append(m.group(1))
    return names


def _inject_component_into_content(
    content: str,
    component_name: str,
    component_path: str,
    page_dir: str,
) -> str | None:
    """Inject import and JSX mount for a component into existing file content.

    Returns modified content or None if injection fails.
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


def _get_words_from_name(name: str) -> set[str]:
    """Split a PascalCase or kebab-case name into words."""
    words: set[str] = set()
    # CamelCase split
    for m in re.finditer(r'[A-Z][a-z]*|[a-z]+|\d+', name):
        words.add(m.group().lower())
    # kebab/camel/snake split
    for part in re.split(r'[-_/\s]', name):
        if part:
            for m in re.finditer(r'[A-Z][a-z]*|[a-z]+|\d+', part):
                words.add(m.group().lower())
    return words


INFRA_ANCHOR_TYPES = frozenset({"route", "layout"})


def _score_semantic_match(
    component_name: str, anchor_name: str, skip_domain: bool = False,
) -> float:
    """Score how well a component name matches the anchor page context.

    Uses deterministic word-overlap scoring. Returns 0.0 to 1.0.
    When *skip_domain* is True (infrastructure anchors like routes/layouts),
    domain-affinity bonuses are not applied — only direct word overlap counts.
    """
    cname = component_name.lower()
    aname = anchor_name.lower()

    if cname == aname:
        return 1.0

    c_words = _get_words_from_name(component_name)
    a_words = _get_words_from_name(anchor_name)

    if not c_words or not a_words:
        return 0.0

    # Direct word overlap (Jaccard)
    intersection = c_words & a_words
    union = c_words | a_words
    base = len(intersection) / len(union) if union else 0.0

    # Bonus: anchor name contains component name or vice versa
    if aname.startswith(cname) or cname.startswith(aname):
        return min(1.0, base + 0.3)

    # Check domain affinity — only for feature anchors (pages/sections).
    # Infrastructure anchors (routes/layouts) are shells — their names are
    # type descriptors, not domain context. Applying domain affinity to them
    # creates false positives (e.g. "DashboardLayout" matching "filter" via
    # the "dashboard" domain keywords).
    domain_bonus = 0.0
    if not skip_domain:
        for domain, keywords in DOMAIN_AFFINITIES.items():
            if domain in a_words and keywords & c_words:
                domain_bonus = max(domain_bonus, 0.2)
            if domain in c_words and keywords & a_words:
                domain_bonus = max(domain_bonus, 0.2)

    return min(1.0, base + domain_bonus)


def _score_structural_proximity(component_path: str, anchor_path: str) -> float:
    """Score filesystem proximity between component and anchor.

    Returns 0.0 to 1.0:
      0.9: same directory
      0.7: shared grandparent (common prefix >= 3)
      0.5: shared great-grandparent (common prefix >= 2)
      0.3: shared root prefix
      0.1: completely different branches
    """
    comp_dir = os.path.dirname(os.path.normpath(component_path))
    anch_dir = os.path.dirname(os.path.normpath(anchor_path))

    if comp_dir == anch_dir:
        return 0.9

    comp_parts = comp_dir.split(os.sep)
    anch_parts = anch_dir.split(os.sep)

    common = 0
    for c, a in zip(comp_parts, anch_parts):
        if c == a:
            common += 1
        else:
            break

    if common >= 3:
        return 0.7
    elif common >= 2:
        return 0.5
    elif common >= 1:
        return 0.3
    return 0.1


def _score_ui_locality(
    component_name: str,
    existing_imports: list[str],
) -> float:
    """Score UI locality — whether similar components already live on this page.

    Returns 0.0 to 1.0:
      0.9: matching component names exist on the page
      0.7: overlapping words exist
      0.3: no relation detected
    """
    if not existing_imports:
        return 0.3

    c_words = _get_words_from_name(component_name)
    if not c_words:
        return 0.3

    for imp in existing_imports:
        if imp.lower() == component_name.lower():
            return 0.9
        i_words = _get_words_from_name(imp)
        if c_words & i_words:
            return 0.7

    return 0.3


def _score_candidate(
    candidate: AnchorCandidate,
    component_name: str,
    component_path: str,
) -> float:
    """Compute deterministic weighted score for a candidate anchor."""
    skip_domain = candidate.type in INFRA_ANCHOR_TYPES
    sem = _score_semantic_match(component_name, candidate.page_name, skip_domain)
    struct = _score_structural_proximity(component_path, candidate.file_path)
    ui_loc = _score_ui_locality(component_name, candidate.existing_components)

    total = (sem * W_SEMANTIC + struct * W_STRUCTURAL + ui_loc * W_UI_LOCALITY)
    candidate.score = total

    logger.debug(
        "ANCHOR_SCORE: component=%s anchor=%s "
        "semantic=%.3f structural=%.3f ui=%.3f total=%.3f",
        component_name, candidate.page_name,
        sem, struct, ui_loc, total,
    )

    return total


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


# ── Invariant: audit only, never input ─────────────────────────────────
# AnchorResolutionDecisions is produced by resolve_anchors() and flows
# exclusively into meta.audit.anchor_resolution for post-hoc diagnostics.
# It MUST NOT be read back as input to any decision — not by the anchor
# resolver itself, not by any downstream module. The decisions dict is
# a trace of what happened, not a signal for what should happen next.
# If a future change needs re-resolution or re-scoring, call the scoring
# functions directly (they are pure) rather than introspecting the audit.
AnchorResolutionDecisions = dict[str, dict]


def resolve_anchors(
    all_fileops: list[FileOp],
    structural_index: StructuralIndex,
    workspace: str,
) -> tuple[list[FileOp], list[str], AnchorResolutionDecisions]:
    """Resolve anchors for all mountable CREATE FileOps and produce MODIFY
    FileOps.

    Args:
        all_fileops: All FileOps from the pipeline (both CREATE and others).
        structural_index: StructuralIndex of the workspace.
        workspace: Workspace root path.

    Returns:
        (modify_ops, unresolved, decisions): MODIFY FileOps for anchor injection,
        list of unresolved component paths, and a dict with full resolution
        decisions keyed by component name for audit visibility.
    """
    create_fileops = [fop for fop in all_fileops if fop.action == "create"]
    mountable = [fop for fop in create_fileops if _is_mountable_ui_component(fop)]

    if not mountable:
        return [], [], {}

    modify_ops: list[FileOp] = []
    unresolved: list[str] = []
    decisions: AnchorResolutionDecisions = {}

    # Build anchor candidates (once, for all components)
    anchors: list[AnchorCandidate] = []

    # 1. Route anchor
    router_path = _find_router_file(workspace)
    if router_path:
        existing = _extract_existing_imports(router_path, workspace)
        anchors.append(AnchorCandidate(
            type="route",
            file_path=router_path,
            page_name="Router",
            existing_components=existing,
        ))

    # 2. Layout anchors
    for lc in _find_layouts(workspace):
        lc.existing_components = _extract_existing_imports(lc.file_path, workspace)
        anchors.append(lc)

    # 3. Feature page anchors
    seen_pages: set[str] = set()
    for pc in _find_pages_from_index(structural_index):
        if pc.file_path not in seen_pages:
            seen_pages.add(pc.file_path)
            pc.existing_components = _extract_existing_imports(pc.file_path, workspace)
            anchors.append(pc)
    for pc in _find_pages_from_filesystem(workspace):
        if pc.file_path not in seen_pages:
            seen_pages.add(pc.file_path)
            pc.existing_components = _extract_existing_imports(pc.file_path, workspace)
            anchors.append(pc)

    # 4. Section anchors
    for sc in _find_section_files(workspace):
        sc.existing_components = _extract_existing_imports(sc.file_path, workspace)
        anchors.append(sc)

    if not anchors:
        logger.warning("ANCHOR_RESOLVER: no anchor candidates found in workspace")
        return [], [fop.path for fop in mountable], {}

    logger.debug("ANCHOR_CANDIDATES: found %d anchor(s)", len(anchors))
    for a in anchors:
        logger.debug(
            "ANCHOR_CANDIDATE: type=%s path=%s",
            a.type, a.file_path,
        )

    # Type priority for tiebreaking and type bonus
    # Infrastructure anchors (route/layout) get NO type bonus — they are
    # last-resort shells. A feature page with domain context must always
    # beat an infrastructure anchor regardless of type priority.
    type_priority = {"route": 4, "layout": 3, "feature_page": 2, "section": 1}
    type_bonus = {"route": 0.0, "layout": 0.0, "feature_page": 0.1, "section": 0.0}

    for fop in mountable:
        component_name = extract_component_name(fop.path)
        if not component_name:
            continue

        # Score every anchor and apply type bonus
        for anchor in anchors:
            _score_candidate(anchor, component_name, fop.path)
            anchor.score += type_bonus.get(anchor.type, 0.0)

        # Sort by score desc, then type priority desc
        anchors.sort(key=lambda a: (a.score, type_priority.get(a.type, 0)), reverse=True)
        best = anchors[0]

        logger.info(
            "ANCHOR_RESOLVER: %s → %s (type=%s, score=%.3f)",
            component_name, best.page_name, best.type, best.score,
        )

        # If score is too low, try layout fallback
        if best.score < MIN_SCORE:
            layout_anchor = next(
                (a for a in anchors if a.type == "layout"),
                None,
            )
            if layout_anchor and layout_anchor.score > 0:
                best = layout_anchor
                logger.info(
                    "ANCHOR_RESOLVER: %s fallback to layout %s (score=%.3f)",
                    component_name, best.page_name, best.score,
                )
            else:
                # Last resort: use the best-scoring page anchor
                page_anchor = next(
                    (a for a in anchors if a.type in ("feature_page", "route")),
                    None,
                )
                if page_anchor:
                    best = page_anchor
                    logger.info(
                        "ANCHOR_RESOLVER: %s fallback to page %s (score=%.3f)",
                        component_name, best.page_name, best.score,
                    )
                else:
                    unresolved.append(fop.path)
                    logger.warning(
                        "ANCHOR_RESOLVER: %s unresolved "
                        "(best score=%.3f, no layout/page fallback)",
                        component_name, best.score,
                    )
                    continue

        # Generate MODIFY op
        modify_op = _generate_modify_op(
            fop, best, workspace, all_fileops,
        )
        if modify_op is not None:
            # ── Merge with existing MODIFY ops (e.g. from renderer's Phase 6) ──
            # Two MODIFY ops for the same path would cause a collision:
            # the last writer wins and overwrites previous content.
            # Instead, inject into the existing MODIFY op's content in-place.
            existing_modify_idx = next(
                (i for i, existing in enumerate(all_fileops)
                 if existing.action == "modify" and existing.path == modify_op.path),
                None,
            )
            if existing_modify_idx is not None:
                # Replace existing MODIFY op (e.g. renderer's Phase 6) in-place.
                # _generate_modify_op already based its injection on the
                # existing MODIFY content via _get_content_for_anchor_path.
                existing = all_fileops[existing_modify_idx]
                all_fileops[existing_modify_idx] = FileOp(
                    action="modify",
                    path=modify_op.path,
                    content=modify_op.content,
                    pipeline_route=existing.pipeline_route,
                    metadata={
                        **(existing.metadata or {}),
                        "anchor_type": best.type,
                        "anchor_page": best.page_name,
                        "component": component_name,
                    },
                )
            else:
                modify_ops.append(modify_op)
        else:
            unresolved.append(fop.path)
            logger.warning(
                "ANCHOR_RESOLVER: %s failed to generate MODIFY op for %s",
                component_name, best.file_path,
            )

        # Collect decision data for audit
        candidates_list = [
            {
                "path": a.file_path,
                "type": a.type,
                "score": round(a.score, 3),
            }
            for a in anchors
        ]
        decisions[component_name] = {
            "selected": {
                "path": best.file_path,
                "type": best.type,
                "score": round(best.score, 3),
            },
            "candidates": candidates_list,
        }

    return modify_ops, unresolved, decisions


def _generate_modify_op(
    component_op: FileOp,
    anchor: AnchorCandidate,
    workspace: str,
    all_fileops: list[FileOp],
) -> FileOp | None:
    """Generate a single MODIFY FileOp that injects a component into an anchor.

    Uses _get_content_for_anchor_path which checks pipeline fileops (CREATE
    and MODIFY) before falling back to disk. This ensures Phase 6 data flow
    from the renderer's MODIFY op is preserved when merging.
    """
    component_name = extract_component_name(component_op.path)
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
