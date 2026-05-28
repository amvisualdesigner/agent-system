"""Constraint Graph — matching integration tests.

These tests validate the end-to-end flow:
  RepositoryIndexer → IntentFileMatcher → IdentityResolver → RepositoryAwareRenderer

They verify that the Phase 1 identity-first matching correctly
produces UPDATE for existing files and CREATE for new ones.

Requires: REPO_ROOT env var.
"""

import pytest

from app.graphir.backends import BackendConfig
from app.graphir.constraint import (
    ExecutionContext,
    Decision,
    PipelineState,
    RenderContext,
)
from app.graphir.constraint.indexer import RepositoryIndexer
from app.graphir.constraint.matcher import IntentFileMatcher
from app.graphir.constraint.resolver import IdentityResolver
from app.graphir.constraint.renderer import RepositoryAwareRenderer

from tests.helpers import FakeWorkspace, make_sample_graph


class TestMatchingGreenfield:
    """Phase 1 baseline: empty workspace → all CREATE.

    This should match the Phase 0 behavioral contract exactly:
    no existing files = no UPDATE decisions.
    """

    def test_kpi_creates(self):
        graph, layout = make_sample_graph("kpi")
        with FakeWorkspace() as ws:
            ctx = ExecutionContext(run_id="test", workspace_root=ws.root)
            config = BackendConfig()

            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)

            matcher = IntentFileMatcher()
            resolver = IdentityResolver()
            identities, candidates = matcher.match(graph, fn)
            decisions = resolver.resolve(identities, candidates, fn)

            renderer = RepositoryAwareRenderer()
            state = PipelineState(file_nodes=fn, component_nodes=cn, decisions=decisions, exec_ctx=ctx)
            fileops = renderer.render(graph, layout, config, context=RenderContext(execution=state))

        assert all(d.decision == Decision.CREATE for d in decisions.values())
        assert all(fop.action == "create" for fop in fileops)




class TestMatchingWithExistingFiles:
    """Phase 1 new behavior: existing files → UPDATE decisions."""

    KPI_TSX = """import React from 'react';

interface KpiRowProps {
  metrics: string[];
}

export const KpiRow: React.FC<KpiRowProps> = ({ metrics }) => {
  return (
    <div className="kpi-row">
      {metrics.map(m => <div key={m}>{m}</div>)}
    </div>
  );
};
"""

    def test_exact_file_match_updates(self):
        """File with matching stem → UPDATE decision."""
        graph, layout = make_sample_graph("kpi")
        with FakeWorkspace() as ws:
            ws.add_file("src/components/KpiRow.tsx", self.KPI_TSX)

            ctx = ExecutionContext(run_id="test", workspace_root=ws.root)
            config = BackendConfig()

            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)

            matcher = IntentFileMatcher()
            resolver = IdentityResolver()
            identities, candidates = matcher.match(graph, fn)
            decisions = resolver.resolve(identities, candidates, fn)

        assert len(decisions) == 1
        d = decisions[list(decisions.keys())[0]]
        assert d.decision == Decision.UPDATE, \
            f"Expected UPDATE for existing KpiRow.tsx, got {d.decision}"
        assert "KpiRow" in d.target_file
        assert d.confidence >= 0.55





    def test_multiple_intents_mixed_decisions(self):
        """Some intents match existing files → UPDATE,
        some don't → CREATE."""
        graph, layout = make_sample_graph("dashboard")
        with FakeWorkspace() as ws:
            ws.add_file("src/KpiRow.tsx", self.KPI_TSX)

            ctx = ExecutionContext(run_id="test", workspace_root=ws.root)
            config = BackendConfig()

            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)

            matcher = IntentFileMatcher()
            resolver = IdentityResolver()
            identities, candidates = matcher.match(graph, fn)
            decisions = resolver.resolve(identities, candidates, fn)

        kpi_decisions = [d for d in decisions.values() if "KpiRow" in d.target_file]
        other_decisions = [d for d in decisions.values() if "KpiRow" not in d.target_file]

        if kpi_decisions:
            assert kpi_decisions[0].decision == Decision.UPDATE

        for d in other_decisions:
            assert d.decision == Decision.CREATE, \
                f"Expected CREATE for {d.target_file}, got {d.decision}"


class TestMatchingDeterminism:
    """Same inputs → same decisions regardless of execution context."""

    def test_deterministic_with_existing_files(self):
        """Running twice on identical workspace → identical decisions."""
        graph, layout = make_sample_graph("kpi")

        with FakeWorkspace() as ws:
            ws.add_file("src/KpiRow.tsx",
                        "export const KpiRow = () => null;")

            ctx = ExecutionContext(run_id="test", workspace_root=ws.root)
            config = BackendConfig()

            # Run 1
            indexer1 = RepositoryIndexer()
            fn1, cn1 = indexer1.index(ws.root)
            matcher1 = IntentFileMatcher()
            resolver1 = IdentityResolver()
            id1, c1 = matcher1.match(graph, fn1)
            d1 = resolver1.resolve(id1, c1, fn1)
            renderer1 = RepositoryAwareRenderer()
            state1 = PipelineState(file_nodes=fn1, component_nodes=cn1, decisions=d1, exec_ctx=ctx)
            fops1 = renderer1.render(graph, layout, config, context=RenderContext(execution=state1))

            # Run 2
            indexer2 = RepositoryIndexer()
            fn2, cn2 = indexer2.index(ws.root)
            matcher2 = IntentFileMatcher()
            resolver2 = IdentityResolver()
            id2, c2 = matcher2.match(graph, fn2)
            d2 = resolver2.resolve(id2, c2, fn2)
            renderer2 = RepositoryAwareRenderer()
            state2 = PipelineState(file_nodes=fn2, component_nodes=cn2, decisions=d2, exec_ctx=ctx)
            fops2 = renderer2.render(graph, layout, config, context=RenderContext(execution=state2))

        for node_id in d1:
            assert d1[node_id].decision == d2[node_id].decision
            assert d1[node_id].target_file == d2[node_id].target_file
            assert abs(d1[node_id].confidence - d2[node_id].confidence) < 0.001

        for f1, f2 in zip(fops1, fops2):
            assert f1.action == f2.action
            assert f1.path == f2.path
