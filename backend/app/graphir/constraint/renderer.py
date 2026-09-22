"""RepositoryAwareRenderer — produces FileOps from decisions (PURE).

PURE function: (GraphIR + UI decisions + content snapshot) → FileOps.
NO filesystem reads, NO git knowledge, NO worktree concepts.

Content generation is delegated to ContentGenerator (pure).
Structural merge is delegated to StructuralDiffEngine (pure).
All existing file content arrives via existing_content_by_path.

Phase 5a: Feature flag constraint_graph_line_range enables
line-range merge via ComponentBoundary.

Phase 6b (Composition Materialization):
  Replaces __COMPOSITION__ placeholder with real React imports and
  mounted child components. Import paths are computed relative from
  parent file to child file using decisions.target_file and split_plan.
  Child JSX is wrapped according to layout constraints.
"""

from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Any

from app.binding.models import ResolvedBindings
from app.graphir.backends import ReactBackend
from app.graphir.backends.react_backend import _flatten_tree, JSVariable
from app.graphir.models import FileOp
from app.graphir.compiler import UIIRCompiler
from app.graphir.ui_ir import UIComponentNode, UIGeneratorContext
from app.graphir.constraint.models import (
    Decision, RefactoringPlan, ComponentBoundary,
)
from app.graphir.path_resolver import FilePathResolver
from app.graphir.constraint.generator import ContentGenerator
from app.graphir.constraint.diff import (
    StructuralDiffEngine,
    ExtendStrategy,
)
from app.graphir.constraint.executor import FileOpExecutor
from app.graphir.constraint.context import RenderContext, PipelineState
from app.config.feature_flags import FEATURE_FLAGS

logger = logging.getLogger(__name__)


@dataclass
class StructuralPatch:
    """Parche estructural para aplicar a un componente existente durante MODIFY.

    No contiene TSX — solo las piezas que MODIFY posee (bindings + data wiring).
    El renderer aplica esto sobre el contenido existente sin invocar al generador.
    """
    component_type: str
    props_interface: str | None = None
    extra_types: list[str] = field(default_factory=list)
    data_imports: list[str] = field(default_factory=list)
    rename_map: dict[str, str] = field(default_factory=dict)


def _parse_interface_block(content: str, component_type: str) -> tuple[int, int, str]:
    """Find interface/type block for component_type in content.
    Returns (start_line, end_line, block_text) or raises ValueError."""
    pattern = re.compile(
        rf'(interface\s+{re.escape(component_type)}Props\s*{{[^}}]+}})',
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        raise ValueError(f"No interface block found for {component_type}")
    start = content[:match.start()].count("\n")
    end = start + match.group().count("\n")
    return start, end, match.group(1)


def _insert_after_interface(text: str, block: str) -> str:
    """Insert a type/interface block after the last interface block in text."""
    iface_end = text.rfind("\n}\n")
    if iface_end == -1:
        return text + "\n" + block + "\n"
    next_nl = text.find("\n", iface_end + 1)
    if next_nl == -1:
        return text + "\n" + block + "\n"
    return text[:next_nl + 1] + block + "\n" + text[next_nl + 1:]


def _insert_import(text: str, imp: str) -> str:
    """Insert a new import line after the last React import."""
    lines = text.split("\n")
    last_react_idx = -1
    for i, line in enumerate(lines):
        if "from 'react'" in line or 'from "react"' in line:
            last_react_idx = i
    insert_at = last_react_idx + 1 if last_react_idx >= 0 else 0
    lines.insert(insert_at, imp)
    return "\n".join(lines)


def _build_modify_patch(
    component_type: str,
    sig: dict | None,
    data_imports: list[str],
) -> StructuralPatch:
    """Build a StructuralPatch from signature + shared COMPONENT_DESTRUCTURE registry.

    No generator call — all data comes from existing structures.
    """
    if not sig or not sig.get("props"):
        return StructuralPatch(component_type=component_type)

    from app.graphir.backends.react_backend import COMPONENT_DESTRUCTURE, _reconcile_destructure

    hardcoded = COMPONENT_DESTRUCTURE.get(component_type, "_props")
    destructure_str = "{" + hardcoded + "}"

    _, _, rename_map = _reconcile_destructure(
        sig, destructure_str, [], return_map=True,
    )

    return StructuralPatch(
        component_type=component_type,
        props_interface=sig.get("props"),
        extra_types=sig.get("extra_types", []),
        data_imports=data_imports,
        rename_map=rename_map,
    )


def apply_patch(existing_content: str, patch: StructuralPatch) -> str:
    """Apply a StructuralPatch to existing file content.

    Operates on structural blocks (interface, imports, export) not on
    global regex over file text. The only regex-based step (rename_map)
    is scoped to word-boundary replacements of known prop names.
    """
    lines = existing_content.split("\n")

    # ── Block 1: Interface replacement (structural block) ──
    if patch.props_interface:
        try:
            old_start, old_end, _ = _parse_interface_block(
                existing_content, patch.component_type,
            )
            lines[old_start:old_end + 1] = patch.props_interface.split("\n")
        except ValueError:
            logger.warning("No interface block found for %s — skipping", patch.component_type)

    # ── Block 2: Extra type injection (structural insertion) ──
    if patch.extra_types:
        text = "\n".join(lines)
        for et in patch.extra_types:
            if et.strip() not in text:
                text = _insert_after_interface(text, et)
        lines = text.split("\n")

    # ── Block 3: Data import injection (structural insertion) ──
    if patch.data_imports:
        text = "\n".join(lines)
        for imp in patch.data_imports:
            if imp.strip() not in text:
                text = _insert_import(text, imp)
        lines = text.split("\n")

    # ── Step 4: rename_map — word-boundary scoped replacement ──
    if patch.rename_map:
        for i, line in enumerate(lines):
            for old, new in patch.rename_map.items():
                pattern = re.compile(rf'(?<!\w){re.escape(old)}(?!\w)')
                lines[i] = pattern.sub(new, line)

    return "\n".join(lines)


class RepositoryAwareRenderer:
    """PURE function: (GraphIR + decisions + snapshot) → FileOps.

    Stateless after construction. All pipeline state arrives via
    RenderContext. No filesystem reads, no indexer calls,
    no memory loading — pure rendering only.

    Phase 1: Uses matcher decisions for CREATE/UPDATE/EXTEND.
    - CREATE → FileOp(action="create") with generated content
    - UPDATE → FileOp(action="modify") with same content (full rewrite)
    - EXTEND → FileOp(action="modify") with appended content

    Phase 5a: When constraint_graph_line_range flag is on:
    - UPDATE → FileOp(action="modify") with line-range merge
    - EXTEND → FileOp(action="modify") with append after last boundary
    """

    def __init__(self):
        self.generator = ContentGenerator()
        self.executor: FileOpExecutor | None = None

    def _get_executor(self, workspace_root: str) -> FileOpExecutor:
        if self.executor is None or self.executor.workspace_root != workspace_root:
            self.executor = FileOpExecutor(workspace_root)
        return self.executor

    def render(
        self,
        graph,
        layout,
        config,
        context: RenderContext | None = None,
        existing_content_by_path: dict[str, str] | None = None,
        resolved_bindings: ResolvedBindings | None = None,
    ) -> list[FileOp]:
        """Produce FileOps from GraphIR + RenderContext (PURE).

        All pipeline-derived state (file_nodes, decisions, split_plan,
        deletions) arrives through context.execution. The renderer
        never reads files, indexes, or loads memory.

        All existing file content for line-range merges arrives via
        existing_content_by_path — NO filesystem reads.

        Props are pre-resolved by BindingResolver — contract_params never
        reach the compiler or renderer (PR1 Binding Resolution Architecture).

        Args:
            graph: GraphIR instance
            layout: GraphIRLayout
            config: BackendConfig
            context: RenderContext with PipelineState + feature_flags.
                When None, creates a minimal empty context for backward
                compat with callers not yet ported.
            existing_content_by_path: Pre-read file content keyed by
                relative path. Used for line-range merge. When None or
                empty, treats all files as empty (greenfield behavior).

        Returns:
            list[FileOp] with actions matching decisions
        """
        if context is None:
            context = RenderContext(execution=PipelineState())

        existing_content_by_path = existing_content_by_path or {}

        execution = context.execution or PipelineState()
        decisions = execution.decisions or {}
        split_plan = execution.split_plan or RefactoringPlan()
        file_nodes = execution.file_nodes or {}
        exec_ctx = execution.exec_ctx

        use_line_range = context.feature_flags.get("constraint_graph_line_range",
                                                     FEATURE_FLAGS.get("constraint_graph_line_range", False))

        # Compile UI tree ONCE — single source of truth for rendering
        ui_tree = UIIRCompiler.compile(
            graph, layout,
            resolved_bindings=resolved_bindings,
            component_signatures=config.component_signatures,
        )
        ReactBackend.last_ui_tree = ui_tree
        ui_node_map = RepositoryAwareRenderer._build_flat_map(ui_tree.root)

        fileops: list[FileOp] = []

        for uinode in _flatten_tree(ui_tree.root):
            ReactBackend.add_trace(uinode.id, "entered", component=uinode.component)

            # instance_only: capability exists in repo, requested as CREATE.
            # Skip file generation — the implementation file stays untouched.
            # The node still participates in parent composition.
            if uinode.instance_only:
                ReactBackend.add_trace(uinode.id, "skipped_instance_only",
                    component=uinode.component)
                continue

            # BINDING_MISSING: required prop has no binding — halt render.
            if uinode.binding_missing_props:
                ReactBackend.add_trace(uinode.id, "binding_missing",
                    component=uinode.component,
                    props={"missing_props": list(uinode.binding_missing_props)})
                logger.error(
                    "BINDING_MISSING: %s missing required props: %s — skipping render. "
                    "Add binding to data_access.json.",
                    uinode.component, uinode.binding_missing_props,
                )
                continue

            decision = decisions.get(uinode.id)
            if decision is None:
                continue

            ui_node = uinode

            # Wrap in adapter — no GraphIRNode reaches generators
            ctx = UIGeneratorContext(
                id=ui_node.id,
                type=ui_node.component,
                data=dict(ui_node.props),
            )

            file_path = decision.target_file or FilePathResolver.resolve(ctx, config)

            # 1) Generate content (or apply StructuralPatch for MODIFY)
            # Pages with Phase 6 data flow always need the generator —
            # StructuralPatch can't regenerate _pageData prop bindings.
            needs_data_flow = uinode.component == "Page" and ui_tree.page_data_source
            existing = existing_content_by_path.get(decision.target_file) if (
                decision.decision in (Decision.UPDATE, Decision.EXTEND)
                and not needs_data_flow
            ) else None
            if existing:
                # MODIFY — no generator call, build patch from signature + registry
                sig = (config.component_signatures or {}).get(ctx.type)
                patch = _build_modify_patch(
                    ctx.type, sig, list(uinode.data_imports),
                )
                content = apply_patch(existing, patch)
            else:
                try:
                    content = self.generator.generate(ctx, layout, config)
                except KeyError:
                    logger.warning(
                        "No generator for type '%s' — skipping node '%s'",
                        ctx.type, ctx.id,
                    )
                    continue

            # PR3: props come from ResolvedBindings.component_props (set in compiler).
            page_hook_decl: str | None = None
            if uinode.component == "Page" and ui_tree.page_data_source:
                page_hook_decl = ReactBackend._page_hook_declaration(ui_tree.page_data_source)
                hook_import = ReactBackend._page_hook_import(ui_tree.page_data_source)
                if hook_import and hook_import not in uinode.data_imports:
                    uinode.data_imports = tuple(list(uinode.data_imports) + [hook_import])

            # Phase 6b: Materialize composition — real imports + React tree
            child_ids = RepositoryAwareRenderer.resolve_children(uinode)
            if child_ids:
                import_block, mount_block, child_data_imports = self._materialize_composition(
                    ctx.id, child_ids, layout, config,
                    decisions, split_plan, ui_node_map,
                )
                content = self._insert_imports(content, import_block)
                content = content.replace("__COMPOSITION__", mount_block)
            else:
                child_data_imports = []
                content = content.replace("__COMPOSITION__", "")

            # Phase 6: inject Page hook declaration after composition materialized
            if page_hook_decl:
                content = ReactBackend._inject_hook_declarations(content, [page_hook_decl])

            # Phase 5: inject data_access.json imports (Page only in Phase 6)
            if uinode.data_imports:
                content = ReactBackend._inject_data_imports(content, uinode.data_imports)

            # Fix 1 — Export normalization: rename export to match filename
            file_basename = os.path.splitext(os.path.basename(file_path))[0]
            if uinode.component != file_basename and file_basename:
                content = ReactBackend._normalize_export_name(content, uinode.component, file_basename)

            # 2) Phase 4: Check for SPLIT redirect
            if split_plan.is_splitting(decision.target_file):
                new_path = split_plan.new_file_for(
                    decision.target_file, ctx.type,
                )
                if new_path:
                    ReactBackend.add_trace(ctx.id, "emitted", component=ctx.type)
                    fileops.append(FileOp(
                        action="create",
                        path=new_path,
                        content=content,
                    ))
                    continue

            if use_line_range:
                # Phase 5a: Line-range merge via StructuralDiffEngine
                fn = file_nodes.get(decision.target_file)
                boundaries = fn.component_boundaries if fn else []
                boundary = self._find_boundary(boundaries, ctx.type)

                if decision.decision in (Decision.UPDATE, Decision.EXTEND):
                    extend_strategy = self.generator.get_extend_strategy(ctx.type)
                    raw = existing_content_by_path.get(decision.target_file, "")
                    existing_lines = raw.split("\n") if raw else []

                    edit = StructuralDiffEngine.compute_edit(
                        content, decision.target_file, existing_lines,
                        boundary, decision.decision,
                        all_boundaries=boundaries,
                        extend_strategy=extend_strategy,
                    )
                    if exec_ctx:
                        executor = self._get_executor(exec_ctx.workspace_root)
                        new_ops = executor.execute(
                            edit, existing_content=raw,
                        )
                        if new_ops:
                            ReactBackend.add_trace(ctx.id, "emitted", component=ctx.type)
                        fileops.extend(new_ops)
                    continue

                # CREATE and SPLIT fall through to legacy handling
                if decision.render_mode:
                    ReactBackend.add_trace(ctx.id, "emitted", component=ctx.type)
                    fileops.append(FileOp(
                        action=decision.render_mode,
                        path=file_path,
                        content=content,
                    ))
                    continue

            # Legacy path — render_mode ya resuelto por apply_engine
            if decision.render_mode:
                ReactBackend.add_trace(ctx.id, "emitted", component=ctx.type)
                fileops.append(FileOp(
                    action=decision.render_mode,
                    path=file_path,
                    content=content,
                ))

        # ── Audit: annotate each FileOp with pipeline route ──
        route = exec_ctx.active_route if exec_ctx else "unknown"
        for fop in fileops:
            fop.pipeline_route = route

        return fileops

    @staticmethod
    def resolve_children(uinode: UIComponentNode) -> list[str]:
        return [c.id for c in uinode.children]

    @staticmethod
    def _build_flat_map(root: UIComponentNode) -> dict[str, UIComponentNode]:
        """BFS flatten UIComponentTree: node.id → UIComponentNode."""
        result: dict[str, UIComponentNode] = {}
        def walk(n: UIComponentNode) -> None:
            result[n.id] = n
            for c in n.children:
                walk(c)
        walk(root)
        return result

    @staticmethod
    def _materialize_composition(
        parent_node_id: str,
        child_ids: list[str],
        layout,
        config,
        decisions: dict[str, object],
        split_plan: RefactoringPlan | None = None,
        ui_node_map: dict[str, UIComponentNode] | None = None,
    ) -> tuple[str, str, list[str]]:
        """Generate real React imports and mount JSX for children.

        Phase 6b: Uses UIComponentTree (via ui_node_map) for child data,
        NEVER accesses graph.nodes directly. Props come from UIComponentNode.props.

        Returns:
            (import_block, mount_block, child_data_imports) — strings + imports to inject.
        """
        imports: list[str] = []
        mounts: list[str] = []
        child_data_imports: list[str] = []

        parent_decision = decisions.get(parent_node_id)
        parent_file: str = ""
        if parent_decision is not None:
            parent_file = getattr(parent_decision, "target_file", "")

        parent_dir = os.path.dirname(parent_file) if parent_file else ""

        ui_node_map = ui_node_map or {}

        for cid in child_ids:
            ui_node = ui_node_map.get(cid)
            if ui_node is None:
                continue

            # Collect data_access.json imports from children
            if ui_node.data_imports:
                child_data_imports.extend(ui_node.data_imports)

            child_decision = decisions.get(cid)
            if child_decision is None:
                continue

            child_file = getattr(child_decision, "target_file", "")
            if not child_file:
                continue

            if split_plan and split_plan.is_splitting(child_file):
                redirected = split_plan.new_file_for(child_file, ui_node.component)
                if redirected:
                    child_file = redirected

            child_stem = os.path.splitext(child_file)[0]
            rel_path = os.path.relpath(child_stem, parent_dir) if parent_dir else f"./{child_stem}"
            if not rel_path.startswith("."):
                rel_path = "./" + rel_path

            imports.append(f"import {{{ui_node.component}}} from '{rel_path}';")

            # Filter props against known signature to avoid type mismatches
            known = ReactBackend._known_prop_names(ui_node.component, config)
            if known is not None and ui_node.props:
                unrecognized = [k for k in ui_node.props if k not in known]
                if unrecognized:
                    logger.debug(
                        "PROP_FILTER component=%s unrecognized=%s known=%s",
                        ui_node.component, unrecognized, list(known),
                    )
            filtered_props = {k: v for k, v in ui_node.props.items() if known is None or k in known} if ui_node.props else ui_node.props
            props_str = ReactBackend._emit(filtered_props)
            child_constraints = layout.constraints.get(cid, [])

            child_tag = (
                f"<{ui_node.component} {props_str} />" if props_str
                else f"<{ui_node.component} />"
            )

            if child_constraints:
                open_tag, close_tag = ReactBackend._layout_to_wrapper(child_constraints)
                mounts.append(f"{open_tag}\n        {child_tag}\n      {close_tag}")
            else:
                mounts.append(child_tag)

        import_block = "\n".join(imports)
        mount_block = "\n".join(mounts)
        return import_block, mount_block, child_data_imports

    @staticmethod
    def _insert_imports(content: str, import_block: str) -> str:
        """Insert import_block after the last import line in content."""
        if not import_block:
            return content
        lines = content.split("\n")
        last_import_idx = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("import ") and stripped.endswith(";"):
                last_import_idx = i
        if last_import_idx >= 0:
            lines.insert(last_import_idx + 1, "")
            lines.insert(last_import_idx + 2, import_block)
        else:
            lines.insert(0, import_block)
            lines.insert(1, "")
        return "\n".join(lines)

    @staticmethod
    def _find_boundary(
        boundaries: list[ComponentBoundary], node_type: str,
    ) -> ComponentBoundary | None:
        """Find the component boundary matching a node type name."""
        for b in boundaries:
            if b.name == node_type:
                return b
        return None


