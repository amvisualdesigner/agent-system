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
import logging

from app.graphir.backends import ReactBackend
from app.graphir.backends.react_backend import _flatten_tree
from app.graphir.models import FileOp
from app.graphir.compiler import UIIRCompiler
from app.graphir.ui_ir import UIComponentNode, UIGeneratorContext
from app.graphir.constraint.models import (
    Decision, RefactoringPlan, ComponentBoundary,
)
from app.graphir.constraint.resolver import IdentityResolver
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
    ) -> list[FileOp]:
        """Produce FileOps from GraphIR + RenderContext (PURE).

        All pipeline-derived state (file_nodes, decisions, split_plan,
        deletions) arrives through context.execution. The renderer
        never reads files, indexes, or loads memory.

        All existing file content for line-range merges arrives via
        existing_content_by_path — NO filesystem reads.

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
        ui_tree = UIIRCompiler.compile(graph, layout)
        ui_node_map = RepositoryAwareRenderer._build_flat_map(ui_tree.root)

        fileops: list[FileOp] = []

        for uinode in _flatten_tree(ui_tree.root):
            ReactBackend.add_trace(uinode.id, "entered", component=uinode.component)
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

            # 1) Generate content
            try:
                content = self.generator.generate(ctx, layout, config)
            except KeyError:
                logger.warning(
                    "No generator for type '%s' — skipping node '%s'",
                    ctx.type, ctx.id,
                )
                continue

            # Phase 6b: Materialize composition — real imports + React tree
            child_ids = RepositoryAwareRenderer.resolve_children(uinode)
            if child_ids:
                import_block, mount_block = self._materialize_composition(
                    ctx.id, child_ids, layout, config,
                    decisions, split_plan, ui_node_map,
                )
                content = self._insert_imports(content, import_block)
                content = content.replace("__COMPOSITION__", mount_block)

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
            fop.metadata["pipeline_route"] = route

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
    ) -> tuple[str, str]:
        """Generate real React imports and mount JSX for children.

        Phase 6b: Uses UIComponentTree (via ui_node_map) for child data,
        NEVER accesses graph.nodes directly. Props come from UIComponentNode.props.

        Returns:
            (import_block, mount_block) — strings to inject into content.
        """
        imports: list[str] = []
        mounts: list[str] = []

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

            props_str = ReactBackend._emit(ui_node.props)
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
        return import_block, mount_block

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


