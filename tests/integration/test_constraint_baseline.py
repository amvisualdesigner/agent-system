"""Constraint Graph — baseline behavioral expectation.

THIS IS THE CONTRACT FOR PHASE 1.

These tests capture the CURRENT (Phase 0 stub) behavior of the
Constraint Graph pipeline: all decisions are CREATE, all FileOp
actions are "create".

Phase 1 (CanonicalIdentity + matching) MUST change this behavior:
- Matching an existing file → UPDATE
- Known identity in semantic memory → UPDATE
- New identity → CREATE (unchanged)

If a Phase 1 change breaks ANY of these baseline tests, the
change is either:
  (a) Correct — update the baseline expectation
  (b) Wrong — don't merge

Run: REPO_ROOT=/tmp python3 -m pytest tests/integration/test_constraint_baseline.py -v

Requires: REPO_ROOT env var, no network, no API.
"""

import os
import pytest

from app.graphir.backends import BackendConfig
from app.graphir.constraint import (
    ExecutionContext,
    Decision,
)
from app.graphir.constraint.indexer import RepositoryIndexer
from app.graphir.constraint.matcher import IntentFileMatcher
from app.graphir.constraint.resolver import IdentityResolver
from app.graphir.constraint.renderer import RepositoryAwareRenderer

from tests.helpers import FakeWorkspace, build_sample_graph


# ================================================================
# Baseline: empty workspace (greenfield)
# ================================================================


class TestBaselineGreenfield:
    """When the workspace is empty, all decisions must be CREATE."""

    def test_kpi_creates_one_file(self):
        """A single KpiRow intent → one CREATE FileOp."""
        graph, layout = build_sample_graph("kpi")
        with FakeWorkspace() as ws:
            ctx = ExecutionContext(run_id="baseline", workspace_root=ws.root)
            config = BackendConfig()

            indexer = RepositoryIndexer()
            file_nodes, component_nodes = indexer.index(ws.root)

            matcher = IntentFileMatcher()
            resolver = IdentityResolver()
            identities, candidates = matcher.match(graph, file_nodes)
            decisions = resolver.resolve(identities, candidates, file_nodes)

            renderer = RepositoryAwareRenderer()
            fileops = renderer.render(
                graph, layout, matcher, file_nodes, component_nodes, ctx, config,
                resolver=resolver,
            )

        # All decisions must be CREATE in greenfield
        for decision in decisions.values():
            assert decision.decision == Decision.CREATE, \
                f"Expected CREATE, got {decision.decision} for {decision.graphir_node_id}"

        # All FileOps must have action "create"
        for fop in fileops:
            assert fop.action == "create", \
                f"Expected action=create, got {fop.action} for {fop.path}"

        assert len(fileops) > 0, "Expected at least one FileOp"

    def test_dashboard_creates_multiple_files(self):
        """A dashboard with 3 intents → 3 distinct CREATE FileOps."""
        graph, layout = build_sample_graph("dashboard")
        with FakeWorkspace() as ws:
            ctx = ExecutionContext(run_id="baseline", workspace_root=ws.root)
            config = BackendConfig()

            indexer = RepositoryIndexer()
            file_nodes, component_nodes = indexer.index(ws.root)

            matcher = IntentFileMatcher()
            resolver = IdentityResolver()
            identities, candidates = matcher.match(graph, file_nodes)
            decisions = resolver.resolve(identities, candidates, file_nodes)

            renderer = RepositoryAwareRenderer()
            fileops = renderer.render(
                graph, layout, matcher, file_nodes, component_nodes, ctx, config,
                resolver=resolver,
            )

        for decision in decisions.values():
            assert decision.decision == Decision.CREATE

        for fop in fileops:
            assert fop.action == "create"

        assert len(fileops) == len(decisions), \
            f"Expected {len(decisions)} FileOps, got {len(fileops)}"


# ================================================================
# Baseline: determinism
# ================================================================


class TestBaselineDeterminism:
    """Same input + same workspace → same output every time."""

    def test_deterministic_decisions(self):
        """Running the pipeline twice on identical inputs must produce
        identical decisions and FileOps."""
        graph, layout = build_sample_graph("kpi")

        with FakeWorkspace() as ws:
            ctx = ExecutionContext(run_id="baseline", workspace_root=ws.root)
            config = BackendConfig()

            # Run 1
            indexer1 = RepositoryIndexer()
            fn1, cn1 = indexer1.index(ws.root)
            matcher1 = IntentFileMatcher()
            resolver1 = IdentityResolver()
            id1, c1 = matcher1.match(graph, fn1)
            d1 = resolver1.resolve(id1, c1, fn1)
            renderer1 = RepositoryAwareRenderer()
            fops1 = renderer1.render(graph, layout, matcher1, fn1, cn1, ctx, config,
                                     resolver=resolver1)

            # Run 2
            indexer2 = RepositoryIndexer()
            fn2, cn2 = indexer2.index(ws.root)
            matcher2 = IntentFileMatcher()
            resolver2 = IdentityResolver()
            id2, c2 = matcher2.match(graph, fn2)
            d2 = resolver2.resolve(id2, c2, fn2)
            renderer2 = RepositoryAwareRenderer()
            fops2 = renderer2.render(graph, layout, matcher2, fn2, cn2, ctx, config,
                                     resolver=resolver2)

        for node_id in d1:
            assert d1[node_id].decision == d2[node_id].decision
            assert d1[node_id].target_file == d2[node_id].target_file
            assert abs(d1[node_id].confidence - d2[node_id].confidence) < 0.001

        for fop1, fop2 in zip(fops1, fops2):
            assert fop1.action == fop2.action
            assert fop1.path == fop2.path


# ================================================================
# Baseline: fileops have content
# ================================================================


class TestBaselineContent:
    """FileOps produced by the stub must have valid content."""

    def test_fileops_have_content(self):
        graph, layout = build_sample_graph("kpi")
        with FakeWorkspace() as ws:
            ctx = ExecutionContext(run_id="baseline", workspace_root=ws.root)
            config = BackendConfig()
            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)
            matcher = IntentFileMatcher()
            resolver = IdentityResolver()
            renderer = RepositoryAwareRenderer()
            fileops = renderer.render(graph, layout, matcher, fn, cn, ctx, config,
                                      resolver=resolver)

        for fop in fileops:
            assert len(fop.content) > 0, f"FileOp {fop.path} has empty content"
            assert fop.path.endswith(".tsx"), f"FileOp {fop.path} has unexpected extension"


# ================================================================
# Baseline: empty workspace produces no files on disk
# ================================================================


class TestBaselineNoSideEffects:
    """The constraint graph pipeline must NOT write files itself.
    File writing is the executor's job (tested separately).
    """

    def test_pipeline_does_not_write_files(self):
        graph, layout = build_sample_graph("table")
        with FakeWorkspace() as ws:
            ctx = ExecutionContext(run_id="baseline", workspace_root=ws.root)
            config = BackendConfig()
            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)
            matcher = IntentFileMatcher()
            resolver = IdentityResolver()
            renderer = RepositoryAwareRenderer()
            renderer.render(graph, layout, matcher, fn, cn, ctx, config,
                            resolver=resolver)

            files_before = ws.list_files()
            assert len(files_before) == 0, \
                f"Pipeline wrote files: {files_before}"
