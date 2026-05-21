"""RepositoryAwareRenderer — produces FileOps from decisions.

Execution Layer: receives a matcher (already populated with decisions),
reads file content for UPDATE/EXTEND merges, produces FileOps.

Content generation is delegated to ContentGenerator.
Structural merge is delegated to StructuralDiffEngine.
IO is isolated to reading existing file content for merges.

Phase 5a: Feature flag constraint_graph_line_range enables
line-range merge via ComponentBoundary.
"""

from __future__ import annotations

import logging
import os

from app.graphir.backends import ReactBackend
from app.graphir.models import FileOp
from app.graphir.constraint.models import Decision, RefactoringPlan, ComponentBoundary
from app.graphir.constraint.resolver import IdentityResolver
from app.graphir.constraint.generator import ContentGenerator
from app.graphir.constraint.diff import (
    StructuralDiffEngine,
    ExtendStrategy,
)
from app.graphir.constraint.executor import FileOpExecutor
from app.config.feature_flags import FEATURE_FLAGS

logger = logging.getLogger(__name__)


class RepositoryAwareRenderer:
    """Execution Layer: produces FileOps from ConstraintGraph decisions.

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
        matcher,
        file_nodes: dict,
        component_nodes: dict,
        exec_ctx,
        config,
        resolver: IdentityResolver | None = None,
        decisions: dict[str, FileOpDecision] | None = None,
        split_plan: RefactoringPlan | None = None,
    ) -> list[FileOp]:
        """Produce FileOps from GraphIR + matcher + resolver.

        Args:
            graph: GraphIR instance
            layout: GraphIRLayout
            matcher: IntentFileMatcher instance (produces candidates)
            file_nodes: dict from indexer
            component_nodes: dict from indexer
            exec_ctx: ExecutionContext
            config: BackendConfig
            resolver: IdentityResolver instance (decides from candidates).
                Defaults to IdentityResolver() for Phase 1.
            decisions: Pre-computed decisions (optional). When provided,
                skips matcher + resolver calls. Used by Phase 2+ callers
                that need access to decisions for memory persistence.
            split_plan: RefactoringPlan from SPLITAnalyzer (Phase 4+).
                When provided, decisions targeting overloaded files may
                be redirected to new files instead.

        Returns:
            list[FileOp] with actions matching decisions
        """
        if decisions is None:
            resolver = resolver or IdentityResolver()
            identities, candidates = matcher.match(graph, file_nodes)
            decisions = resolver.resolve(identities, candidates, file_nodes)
        children_by_source = self._children_map(graph)

        split_plan = split_plan or RefactoringPlan()
        use_line_range = FEATURE_FLAGS.get("constraint_graph_line_range", False)
        fileops: list[FileOp] = []

        for node in graph.nodes.values():
            decision = decisions.get(node.id)
            if decision is None:
                continue

            # 1) Generate content
            try:
                content = self.generator.generate(node, layout, config)
            except KeyError:
                continue

            # Inject composition
            child_ids = children_by_source.get(node.id, [])
            if child_ids:
                composition = self._render_composition(child_ids, graph, config)
                content = self._inject_composition(content, composition)

            # 2) Phase 4: Check for SPLIT redirect
            if split_plan.is_splitting(decision.target_file):
                new_path = split_plan.new_file_for(
                    decision.target_file, node.type,
                )
                if new_path:
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
                boundary = self._find_boundary(boundaries, node.type)

                if decision.decision in (Decision.UPDATE, Decision.EXTEND):
                    extend_strategy = self.generator.get_extend_strategy(node.type)
                    existing_lines = []
                    file_path = os.path.join(
                        exec_ctx.workspace_root, decision.target_file,
                    )
                    if os.path.exists(file_path):
                        with open(file_path) as f:
                            existing_lines = f.read().split("\n")

                    edit = StructuralDiffEngine.compute_edit(
                        content, decision.target_file, existing_lines,
                        boundary, decision.decision,
                        all_boundaries=boundaries,
                        extend_strategy=extend_strategy,
                    )
                    executor = self._get_executor(exec_ctx.workspace_root)
                    fileops.extend(executor.execute(edit))
                    continue

                # CREATE and SPLIT fall through to legacy handling
                if decision.decision in (Decision.CREATE, Decision.SPLIT):
                    fileops.append(FileOp(
                        action="create",
                        path=decision.target_file,
                        content=content,
                    ))
                    continue

            # Legacy path (Phase 1 behavior)
            if decision.decision == Decision.CREATE:
                fileops.append(FileOp(
                    action="create",
                    path=decision.target_file,
                    content=content,
                ))

            elif decision.decision in (Decision.UPDATE, Decision.EXTEND):
                fileops.append(FileOp(
                    action="modify",
                    path=decision.target_file,
                    content=content,
                ))

            elif decision.decision == Decision.SPLIT:
                fileops.append(FileOp(
                    action="create",
                    path=decision.target_file,
                    content=content,
                ))

        return fileops

    @staticmethod
    def _children_map(graph) -> dict[str, list[str]]:
        children: dict[str, list[str]] = {}
        for edge in getattr(graph, "edges", []):
            children.setdefault(edge.source, []).append(edge.target)
        return children

    @staticmethod
    def _render_composition(
        child_ids: list[str], graph, config,
    ) -> str:
        parts: list[str] = []
        for cid in child_ids:
            child = graph.nodes.get(cid)
            if child is None:
                continue
            props_str = ReactBackend._render_props_jsx(child.data)
            parts.append(
                f"<{child.type} {props_str} />" if props_str else f"<{child.type} />"
            )
        return "\n".join(parts)

    @staticmethod
    def _inject_composition(content: str, composition: str) -> str:
        if not composition:
            return content
        return content.replace("__COMPOSITION__", composition)

    @staticmethod
    def _find_boundary(
        boundaries: list[ComponentBoundary], node_type: str,
    ) -> ComponentBoundary | None:
        """Find the component boundary matching a node type name."""
        for b in boundaries:
            if b.name == node_type:
                return b
        return None


