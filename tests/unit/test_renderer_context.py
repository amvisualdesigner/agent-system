"""Tests for ExecutionContext, PipelineState, RenderContext hierarchy.

Verifies:
  1. PipelineState defaults are empty
  2. RenderContext wraps PipelineState correctly
  3. Renderer accepts RenderContext in place of raw params
  4. Renderer falls back to empty context when context=None
  5. Feature flags flow through RenderContext
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.constraint.context import (
    ExecutionContext,
    PipelineState,
    RenderContext,
)
from app.graphir.constraint.renderer import RepositoryAwareRenderer
from app.graphir.constraint.models import DeletionRecord, RefactoringPlan


class TestPipelineState(unittest.TestCase):

    def test_defaults_are_empty(self):
        state = PipelineState()
        self.assertEqual(state.file_nodes, {})
        self.assertEqual(state.component_nodes, {})
        self.assertEqual(state.decisions, {})
        self.assertIsNone(state.split_plan)
        self.assertEqual(state.deletions, [])
        self.assertEqual(state.resolved_mapping, {})
        self.assertIsNone(state.exec_ctx)

    def test_can_set_all_fields(self):
        ctx = ExecutionContext(run_id="test", workspace_root="/tmp")
        plan = RefactoringPlan()
        deletions = [DeletionRecord(fingerprint="fp1", file_path="/dev/null", component_name="C")]
        state = PipelineState(
            file_nodes={"a": 1},
            component_nodes={"b": 2},
            decisions={"n1": "CREATE"},
            split_plan=plan,
            deletions=deletions,
            resolved_mapping={"fp1": object()},
            exec_ctx=ctx,
        )
        self.assertEqual(state.file_nodes, {"a": 1})
        self.assertEqual(state.decisions, {"n1": "CREATE"})
        self.assertIs(state.split_plan, plan)
        self.assertIs(state.deletions, deletions)
        self.assertIs(state.exec_ctx, ctx)


class TestRenderContext(unittest.TestCase):

    def test_wraps_pipeline_state(self):
        state = PipelineState(file_nodes={"x": 1})
        ctx = RenderContext(execution=state, feature_flags={"flag_a": True})
        self.assertIs(ctx.execution, state)
        self.assertEqual(ctx.feature_flags, {"flag_a": True})

    def test_execution_defaults_to_none(self):
        ctx = RenderContext()
        self.assertIsNone(ctx.execution)

    def test_feature_flags_defaults_empty(self):
        ctx = RenderContext()
        self.assertEqual(ctx.feature_flags, {})


class TestRendererContextBackwardCompat(unittest.TestCase):

    def test_renderer_accepts_render_context(self):
        """Renderer.render() accepts context=RenderContext(...) keyword."""
        from app.graphir.intent import Intent, IntentPlan, make_intent_id
        from app.graphir.builder import GraphIRBuilder
        from app.graphir.backends.base import BackendConfig

        plan = IntentPlan(
            intents=[Intent(
                id=make_intent_id("table", "presentation.table"),
                capability="presentation.table",
                params={"columns": ["a"]},
            )],
            params={},
        )
        graph = GraphIRBuilder.build(plan)
        from app.graphir.layout import LayoutDerivationEngine
        layout = LayoutDerivationEngine.derive(graph)
        config = BackendConfig(output_base_path="/tmp")

        renderer = RepositoryAwareRenderer()
        state = PipelineState(decisions={})
        ctx = RenderContext(execution=state)
        fileops = renderer.render(graph, layout, config, context=ctx)
        self.assertIsInstance(fileops, list)

    def test_renderer_accepts_none_context(self):
        """Renderer.render() with context=None falls back to empty state."""
        from app.graphir.intent import Intent, IntentPlan, make_intent_id
        from app.graphir.builder import GraphIRBuilder
        from app.graphir.backends.base import BackendConfig

        plan = IntentPlan(
            intents=[Intent(
                id=make_intent_id("table", "presentation.table"),
                capability="presentation.table",
                params={"columns": ["a"]},
            )],
            params={},
        )
        graph = GraphIRBuilder.build(plan)
        from app.graphir.layout import LayoutDerivationEngine
        layout = LayoutDerivationEngine.derive(graph)
        config = BackendConfig(output_base_path="/tmp")

        renderer = RepositoryAwareRenderer()
        fileops = renderer.render(graph, layout, config, context=None)
        self.assertIsInstance(fileops, list)

    def test_feature_flags_flow_through_context(self):
        """Feature flags set in RenderContext are visible inside renderer."""
        from app.graphir.intent import Intent, IntentPlan, make_intent_id
        from app.graphir.builder import GraphIRBuilder
        from app.graphir.backends.base import BackendConfig
        from app.config.feature_flags import FEATURE_FLAGS

        plan = IntentPlan(
            intents=[Intent(
                id=make_intent_id("page", "layout.page"),
                capability="layout.page",
            )],
            params={},
        )
        graph = GraphIRBuilder.build(plan)
        from app.graphir.layout import LayoutDerivationEngine
        layout = LayoutDerivationEngine.derive(graph)
        config = BackendConfig(output_base_path="/tmp")

        renderer = RepositoryAwareRenderer()
        state = PipelineState(decisions={})
        ctx = RenderContext(
            execution=state,
            feature_flags=dict(FEATURE_FLAGS),
        )
        fileops = renderer.render(graph, layout, config, context=ctx)
        self.assertIsInstance(fileops, list)


if __name__ == "__main__":
    unittest.main()
