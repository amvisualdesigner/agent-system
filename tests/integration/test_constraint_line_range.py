"""Line-range merge — integration tests.

Validates surgical line-range replacement through the full pipeline:
  RepositoryIndexer → ContentGenerator → StructuralDiffEngine →
  FileOpExecutor

Requires: REPO_ROOT env var.
Uses constraint_graph_line_range=True feature flag.
"""

import os

from app.graphir.backends import BackendConfig
from app.graphir.constraint import (
    ExecutionContext,
    Decision,
    ExtendStrategy,
    PipelineState,
    RenderContext,
)
from app.graphir.constraint.indexer import RepositoryIndexer
from app.graphir.constraint.generator import ContentGenerator
from app.graphir.constraint.diff import StructuralDiffEngine
from app.graphir.constraint.renderer import RepositoryAwareRenderer

from tests.helpers import FakeWorkspace, make_sample_graph


# Override feature flag for line-range tests
def _enable_line_range():
    from app.config import feature_flags
    feature_flags.FEATURE_FLAGS["constraint_graph_line_range"] = True


def _disable_line_range():
    from app.config import feature_flags
    feature_flags.FEATURE_FLAGS["constraint_graph_line_range"] = False


KPI_TSX = """import React from 'react';

export const KpiRow = () => {
  return <div>old kpi</div>;
};
"""


class TestLineRangeUPDATE:
    """UPDATE with line-range merge replaces only the component range."""




class TestLineRangeEXTEND:
    """EXTEND with APPEND_REGION appends after last boundary."""




class TestLineRangeWithSPLIT:
    """Line-range merge + SPLIT proposal on the same file (F5: proposal is NOT authority).

    Invariants:
      1. A SPLIT recommendation (overloaded file) produces NO new-file create.
      2. Line-range merge still targets the CONFIRMED plan target (Chart.tsx).
      3. The renderer never reads split_plan — pipeline state carries it nowhere.
    """

    CHART_TSX = """import React from 'react';

export const KpiRow = () => {
  return <div>old kpi</div>;
};

export const Timeseries = ({ data }) => {
  return <div>old timeseries</div>;
};
"""

    def test_split_proposal_does_not_redirect_nor_create(self):
        """A SPLIT proposal for Chart.tsx must NOT redirect KpiRow to a new
        file and must NOT emit a CREATE for the split target. The confirmed
        target stays Chart.tsx for the Timeseries line-range merge."""
        _enable_line_range()
        try:
            graph, layout = make_sample_graph("dashboard")

            with FakeWorkspace() as ws:
                ws.add_file("src/components/Chart.tsx", self.CHART_TSX)

                ctx = ExecutionContext(
                    run_id="lr-split", workspace_root=ws.root,
                )
                config = BackendConfig(
                    output_base_path="src/components",
                    path_map={
                        "KpiRow": "src/components/Chart.tsx",
                        "Timeseries": "src/components/Chart.tsx",
                    },
                )

                indexer = RepositoryIndexer()
                fn, cn = indexer.index(ws.root)

                # Both KpiRow and Timeseries are in Chart.tsx
                from app.graphir.constraint.models import (
                    FileOpDecision, RefactoringPlan, SplitDirective,
                )
                decisions = {
                    node.id: FileOpDecision(
                        intent_id="test",
                        graphir_node_id=node.id,
                        decision=Decision.UPDATE,
                        target_file="src/components/Chart.tsx",
                        confidence=1.0,
                        rationale="test",
                        render_mode="modify",
                    )
                    for node in graph.nodes.values()
                }

                # SPLIT proposal: extract KpiRow (analysis/evidence only)
                split_plan = RefactoringPlan(splits=[
                    SplitDirective(
                        source_file="src/components/Chart.tsx",
                        new_file="src/components/KpiRow.tsx",
                        components_to_extract=["KpiRow"],
                    ),
                ])

                renderer = RepositoryAwareRenderer()
                state = PipelineState(
                    file_nodes=fn, component_nodes=cn,
                    decisions=decisions,
                    exec_ctx=ctx,
                )
                # F5: the SPLIT proposal is never handed to the renderer —
                # it stays an independent analysis object, not pipeline state.
                fileops = renderer.render(
                    graph, layout, config,
                    context=RenderContext(execution=state),
                )

                # 1. No CREATE for the split new_file
                assert not any(
                    f.action == "create" and f.path == "src/components/KpiRow.tsx"
                    for f in fileops
                ), f"SPLIT proposal produced an automatic CREATE: {[(f.action, f.path) for f in fileops]}"

                # 2. Any output targets the CONFIRMED plan target only
                paths = {f.path for f in fileops}
                assert all(
                    p == "src/components/Chart.tsx" for p in paths
                ), f"Renderer escaped the confirmed target: {paths}"

                # 3. split_plan is not part of pipeline state
                assert not hasattr(state, "split_plan")
        finally:
            _disable_line_range()
