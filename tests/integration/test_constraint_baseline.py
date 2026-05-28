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
    PipelineState,
    RenderContext,
)
from app.graphir.constraint.indexer import RepositoryIndexer
from app.graphir.constraint.matcher import IntentFileMatcher
from app.graphir.constraint.resolver import IdentityResolver
from app.graphir.constraint.renderer import RepositoryAwareRenderer

from tests.helpers import FakeWorkspace, make_sample_graph


# ================================================================
# Baseline: determinism
# ================================================================


class TestBaselineDeterminism:
    """Same input + same workspace → same output every time."""

    def test_deterministic_decisions(self):
        """Running the pipeline twice on identical inputs must produce
        identical decisions and FileOps."""
        graph, layout = make_sample_graph("kpi")

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
            state1 = PipelineState(
                file_nodes=fn1, component_nodes=cn1,
                decisions=d1, exec_ctx=ctx,
            )
            fops1 = renderer1.render(graph, layout, config,
                                     context=RenderContext(execution=state1))

            # Run 2
            indexer2 = RepositoryIndexer()
            fn2, cn2 = indexer2.index(ws.root)
            matcher2 = IntentFileMatcher()
            resolver2 = IdentityResolver()
            id2, c2 = matcher2.match(graph, fn2)
            d2 = resolver2.resolve(id2, c2, fn2)
            renderer2 = RepositoryAwareRenderer()
            state2 = PipelineState(
                file_nodes=fn2, component_nodes=cn2,
                decisions=d2, exec_ctx=ctx,
            )
            fops2 = renderer2.render(graph, layout, config,
                                     context=RenderContext(execution=state2))

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
        graph, layout = make_sample_graph("kpi")
        with FakeWorkspace() as ws:
            ctx = ExecutionContext(run_id="baseline", workspace_root=ws.root)
            config = BackendConfig()
            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)
            matcher = IntentFileMatcher()
            resolver = IdentityResolver()
            identities, candidates = matcher.match(graph, fn)
            decisions = resolver.resolve(identities, candidates, fn)
            renderer = RepositoryAwareRenderer()
            state = PipelineState(
                file_nodes=fn, component_nodes=cn,
                decisions=decisions, exec_ctx=ctx,
            )
            fileops = renderer.render(graph, layout, config,
                                      context=RenderContext(execution=state))

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
        graph, layout = make_sample_graph("table")
        with FakeWorkspace() as ws:
            ctx = ExecutionContext(run_id="baseline", workspace_root=ws.root)
            config = BackendConfig()
            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)
            matcher = IntentFileMatcher()
            resolver = IdentityResolver()
            identities, candidates = matcher.match(graph, fn)
            decisions = resolver.resolve(identities, candidates, fn)
            renderer = RepositoryAwareRenderer()
            state = PipelineState(
                file_nodes=fn, component_nodes=cn,
                decisions=decisions, exec_ctx=ctx,
            )
            renderer.render(graph, layout, config,
                            context=RenderContext(execution=state))

            files_before = ws.list_files()
            assert len(files_before) == 0, \
                f"Pipeline wrote files: {files_before}"
