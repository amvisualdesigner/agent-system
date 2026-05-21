"""Constraint Graph — integration smoke tests.

These tests validate that the wiring between ExecutionContext,
GraphIR Purity Boundary, and the decision pipeline works.

They do NOT test logic (scoring, matching, rendering).
They only verify that:
  1. ExecutionContext can be created and guards paths
  2. ExecutionContext survives round-trip through the pipeline
  3. Purity boundary rejects filesystem data in GraphIR nodes
  4. FileOpDecision is produced correctly
  5. Stub modules (indexer, matcher) don't crash on call

These tests require REPO_ROOT env var.
"""

import os
import pytest

from app.graphir.constraint import ExecutionContext, Decision, FileOpDecision
from app.graphir.boundary import enforce_graph_purity, GraphIRBoundaryViolation
from app.graphir.models import GraphIR, GraphIRNode, GraphIRLayout, GraphIRDraft
from app.graphir.intent import Intent, IntentPlan, CapabilityDef, CapabilityCategory
from app.graphir.pipeline import GraphIRPipeline


# ================================================================
# ExecutionContext wiring
# ================================================================


class TestExecutionContextWiring:
    """Smoke: ExecutionContext creation and basic usage."""

    def test_create_with_minimal_args(self):
        ctx = ExecutionContext(run_id="smoke-1", workspace_root="/tmp/test")
        assert ctx.run_id == "smoke-1"
        assert ctx.workspace_root == "/tmp/test"
        assert ctx.worktree_id == "agent-smoke-1"  # auto-derived

    def test_create_with_full_args(self):
        ctx = ExecutionContext(
            run_id="smoke-2",
            workspace_root="/opt/repo",
            worktree_id="custom-wt",
            git_ref="main",
            is_isolated=True,
            artifacts_dir="/tmp/artifacts/smoke-2",
        )
        assert ctx.worktree_id == "custom-wt"
        assert ctx.git_ref == "main"
        assert ctx.artifacts_dir == "/tmp/artifacts/smoke-2"

    def test_memory_path_uses_workspace_root(self):
        ctx = ExecutionContext(run_id="smoke-3", workspace_root="/tmp/repo")
        assert ctx.memory_path == "/tmp/repo/.opencode/semantic_memory.json"

    def test_artifacts_falls_back_to_settings(self):
        ctx = ExecutionContext(run_id="smoke-4", workspace_root="/tmp/repo")
        path = ctx.artifacts
        assert "smoke-4" in path

    def test_guard_allows_valid_path(self):
        ctx = ExecutionContext(run_id="smoke-5", workspace_root="/tmp/repo")
        result = ctx.guard("src/foo.tsx")
        assert result == "src/foo.tsx"

    def test_guard_rejects_path_escape(self):
        ctx = ExecutionContext(run_id="smoke-6", workspace_root="/tmp/repo")
        with pytest.raises(ValueError, match="Path escape"):
            ctx.guard("../../etc/passwd")


# ================================================================
# GraphIR Purity Boundary wiring
# ================================================================


class TestPurityBoundaryWiring:
    """Smoke: purity enforcement against real GraphIR objects."""

    def test_pure_graph_passes(self):
        """A GraphIR built by the pipeline must pass purity check."""
        plan = IntentPlan(
            intents=[
                Intent(id="i1", capability="presentation.kpi_row"),
            ],
        )
        graph, layout = GraphIRPipeline.run(plan)
        enforce_graph_purity(graph)  # should not raise

    def test_contaminated_graph_is_rejected(self):
        """A GraphIR with a file_path in data must be rejected."""
        from app.graphir.models import GraphIR, GraphIRNode, GraphIRLayout
        graph = GraphIR(
            nodes={
                "n1": GraphIRNode(id="n1", type="Page", data={"file_path": "src/Page.tsx"}),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        with pytest.raises(GraphIRBoundaryViolation):
            enforce_graph_purity(graph)


# ================================================================
# FileOpDecision wiring
# ================================================================


class TestFileOpDecisionWiring:
    """Smoke: decision types are constructable and carry contracts."""

    def test_create_decision(self):
        d = FileOpDecision(
            intent_id="kpi",
            graphir_node_id="n1",
            decision=Decision.CREATE,
            target_file="src/KpiRow.tsx",
            confidence=0.8,
            rationale="No existing file found",
        )
        assert d.decision == Decision.CREATE
        assert d.must_not_modify_semantics is True  # invariant

    def test_update_decision(self):
        d = FileOpDecision(
            intent_id="timeseries",
            graphir_node_id="n2",
            decision=Decision.UPDATE,
            target_file="src/Timeseries.tsx",
            confidence=0.95,
        )
        assert d.decision.value == "modify"

    def test_decision_enum_values(self):
        assert Decision.CREATE.value == "create"
        assert Decision.UPDATE.value == "modify"
        assert Decision.EXTEND.value == "extend"
        assert Decision.SPLIT.value == "split"


# ================================================================
# Stub module wiring (Phase 0 stubs must not crash)
# ================================================================


class TestStubModulesWiring:
    """Smoke: Phase 0 stub modules are importable and callable."""

    def test_indexer_stub_returns_empty(self):
        from app.graphir.constraint.indexer import RepositoryIndexer
        indexer = RepositoryIndexer()
        file_nodes, component_nodes = indexer.index("/tmp/test")
        assert file_nodes == {}
        assert component_nodes == {}

    def test_matcher_stub_returns_identities_and_candidates(self):
        from app.graphir.constraint.matcher import IntentFileMatcher
        from app.graphir.constraint.resolver import IdentityResolver
        from app.graphir.models import GraphIR, GraphIRNode, GraphIRLayout

        matcher = IntentFileMatcher()
        resolver = IdentityResolver()
        graph = GraphIR(
            nodes={
                "n1": GraphIRNode(id="n1", type="KpiRow", data={}),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        identities, candidates = matcher.match(graph, {})
        decisions = resolver.resolve(identities, candidates, {})
        assert len(identities) == 1
        assert isinstance(decisions, dict)
        assert decisions["n1"].decision == Decision.CREATE

    def test_renderer_stub_generates_fileops(self):
        from app.graphir.constraint.renderer import RepositoryAwareRenderer
        from app.graphir.constraint.matcher import IntentFileMatcher
        from app.graphir.constraint.resolver import IdentityResolver
        from app.graphir.models import GraphIR, GraphIRNode, GraphIRLayout
        from app.graphir.backends import BackendConfig

        renderer = RepositoryAwareRenderer()
        matcher = IntentFileMatcher()
        resolver = IdentityResolver()
        graph = GraphIR(
            nodes={
                "n1": GraphIRNode(id="n1", type="KpiRow", data={}),
            },
            edges=[],
            layout=GraphIRLayout(root="n1"),
        )
        layout = GraphIRLayout(root="n1")
        config = BackendConfig()
        ctx = ExecutionContext(run_id="smoke", workspace_root="/tmp/test")

        fileops = renderer.render(graph, layout, matcher, {}, {}, ctx, config,
                                  resolver=resolver)
        assert len(fileops) > 0
        for fop in fileops:
            assert hasattr(fop, "action")
            assert hasattr(fop, "path")
            assert hasattr(fop, "content")
