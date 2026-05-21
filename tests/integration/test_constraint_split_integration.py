"""SPLITAnalyzer + Renderer — integration tests.

Validates the end-to-end flow:
  RepositoryIndexer → IntentFileMatcher → IdentityResolver →
  SPLITAnalyzer → RepositoryAwareRenderer(split_plan=...)

Uses resolved_mapping (Phase 2) to force Level 1 match on a
multi-component file, then verifies SPLIT redirection (Phase 4).

Requires: REPO_ROOT env var.
"""

from app.graphir.backends import BackendConfig
from app.graphir.constraint import (
    ExecutionContext,
)
from app.graphir.constraint.indexer import RepositoryIndexer
from app.graphir.constraint.matcher import IntentFileMatcher
from app.graphir.constraint.resolver import IdentityResolver
from app.graphir.constraint.renderer import RepositoryAwareRenderer
from app.graphir.constraint.split_analyzer import SPLITAnalyzer

from tests.helpers import FakeWorkspace, build_sample_graph


class TestSPLITWithRenderer:
    """SPLIT + renderer: decisions redirected to new files."""

    CHART_TSX = """import React from 'react';

export const KpiRow = () => null;

export const Timeseries = ({ data }) => null;

export const ChartHeader = ({ title }) => null;

export const Chart = () => null;
"""

    def test_split_redirects_kpirow_to_separate_file(self):
        """Phase 2 memory + Phase 4 SPLIT: KpiRow destined for Chart.tsx
        gets redirected to new file KpiRow.tsx when Chart.tsx has 4
        components.
        """
        graph, layout = build_sample_graph("kpi")

        with FakeWorkspace() as ws:
            ws.add_file("src/components/Chart.tsx", self.CHART_TSX)

            ctx = ExecutionContext(run_id="split-test", workspace_root=ws.root)
            config = BackendConfig(
                output_base_path="src/components",
                path_map={"KpiRow": "src/components/Chart.tsx"},
            )

            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)

            matcher = IntentFileMatcher()
            identities, candidates = matcher.match(graph, fn)

            # Phase 2: resolved mapping forces KpiRow → Chart.tsx
            fp = identities["KpiRow"].fingerprint()
            resolver = IdentityResolver(
                resolved_mapping={fp: "src/components/Chart.tsx"},
            )
            decisions = resolver.resolve(identities, candidates, fn)

            # Verify Phase 2: Level 1 match → UPDATE on Chart.tsx
            from app.graphir.constraint.models import Decision
            dec = decisions["KpiRow"]
            assert dec.decision == Decision.UPDATE, f"Expected UPDATE, got {dec.decision}"
            assert dec.target_file == "src/components/Chart.tsx"
            assert dec.confidence == 1.0

            # Phase 4: SPLITAnalyzer detects 4-component file
            split_analyzer = SPLITAnalyzer(threshold_component_count=2)
            split_plan = split_analyzer.analyze(decisions, identities, fn)

            assert len(split_plan.splits) == 1
            s = split_plan.splits[0]
            assert s.source_file == "src/components/Chart.tsx"
            assert s.new_file == "src/components/KpiRow.tsx"

            # Phase 4: renderer redirects
            renderer = RepositoryAwareRenderer()
            fileops = renderer.render(
                graph, layout, matcher,
                fn, cn,
                ctx, config,
                resolver=resolver,
                decisions=decisions,
                split_plan=split_plan,
            )

            create_ops = [f for f in fileops if f.action == "create"]
            modify_ops = [f for f in fileops if f.action == "modify"]

            # KpiRow redirected to new file
            assert any(f.path == "src/components/KpiRow.tsx" for f in create_ops), \
                f"Expected create for KpiRow.tsx, got {[f.path for f in create_ops]}"
            # No modify for Chart.tsx (only one graph node, it's the split one)
            assert not any(f.path == "src/components/Chart.tsx" for f in modify_ops)

    def test_no_split_below_threshold(self):
        """When threshold is high, split does not trigger and renderer
        falls back to normal UPDATE on Chart.tsx.
        """
        graph, layout = build_sample_graph("kpi")

        with FakeWorkspace() as ws:
            ws.add_file("src/components/Chart.tsx", self.CHART_TSX)

            ctx = ExecutionContext(run_id="split-no", workspace_root=ws.root)
            config = BackendConfig(
                output_base_path="src/components",
                path_map={"KpiRow": "src/components/Chart.tsx"},
            )

            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)

            matcher = IntentFileMatcher()
            identities, candidates = matcher.match(graph, fn)

            fp = identities["KpiRow"].fingerprint()
            resolver = IdentityResolver(
                resolved_mapping={fp: "src/components/Chart.tsx"},
            )
            decisions = resolver.resolve(identities, candidates, fn)

            # Threshold=5 > 4 components → no split
            split_analyzer = SPLITAnalyzer(threshold_component_count=5)
            split_plan = split_analyzer.analyze(decisions, identities, fn)

            assert len(split_plan.splits) == 0

            renderer = RepositoryAwareRenderer()
            fileops = renderer.render(
                graph, layout, matcher,
                fn, cn,
                ctx, config,
                resolver=resolver,
                decisions=decisions,
                split_plan=split_plan,
            )

            # No split → normal UPDATE modify on Chart.tsx
            modify_ops = [f for f in fileops if f.action == "modify"]
            assert any(f.path == "src/components/Chart.tsx" for f in modify_ops), \
                f"Expected modify for Chart.tsx, got {[f.path for f in modify_ops]}"
