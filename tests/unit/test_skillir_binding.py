"""SkillIR Binding Contract Layer — unit tests.

Tests that SkillIR params are bound to GraphIR nodes per capability schema,
and that missing required keys fail fast via SkillIRBindingError.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import unittest
from app.graphir.binding import (
    CAPABILITY_SCHEMA,
    SkillIRBindingError,
    bind_skillir_to_nodes,
    validate_binding,
    _get_all_schema_keys,
)
from app.graphir.intent import (
    Intent,
    IntentExtensionRegistry,
    IntentPlan,
    make_intent_id,
)
from app.graphir.models import GraphIR, GraphIRDraft, GraphIRNode
from app.graphir.builder import GraphIRBuilder


class TestCapabilitySchema(unittest.TestCase):
    """CAPABILITY_SCHEMA integrity checks."""

    def test_required_keys_have_schema(self):
        for cap, schema in CAPABILITY_SCHEMA.items():
            with self.subTest(capability=cap):
                self.assertIn("required", schema)
                self.assertIn("optional", schema)
                self.assertIsInstance(schema["required"], list)
                self.assertIsInstance(schema["optional"], list)

    def test_presentation_table_requires_columns(self):
        self.assertIn("columns", CAPABILITY_SCHEMA["presentation.table"]["required"])

    def test_presentation_kpi_row_requires_metrics(self):
        self.assertIn("metrics", CAPABILITY_SCHEMA["presentation.kpi_row"]["required"])

    def test_legacy_display_capabilities_have_schemas(self):
        self.assertIn("display.kpi_row", CAPABILITY_SCHEMA)
        self.assertIn("display.analytics_table", CAPABILITY_SCHEMA)
        self.assertIn("display.timeseries", CAPABILITY_SCHEMA)
        self.assertIn("display.filter_panel", CAPABILITY_SCHEMA)


class TestGetAllSchemaKeys(unittest.TestCase):
    def test_returns_both_required_and_optional(self):
        keys = _get_all_schema_keys("presentation.table")
        self.assertIn("columns", keys)
        self.assertIn("table_data", keys)

    def test_unknown_capability_returns_empty(self):
        keys = _get_all_schema_keys("unknown.capability")
        self.assertEqual(keys, set())

    def test_layout_page_returns_empty(self):
        keys = _get_all_schema_keys("layout.page")
        self.assertEqual(keys, set())


class TestBindSkillIRToNodes(unittest.TestCase):
    """bind_skillir_to_nodes — merging plan.params into node.data."""

    def setUp(self):
        IntentExtensionRegistry.clear()

    def test_merges_columns_into_table_node(self):
        draft = GraphIRDraft()
        draft.params = {"columns": ["Hello", "Value", "Change"]}
        draft.add_node(GraphIRNode(
            id="table",
            type="AnalyticsTable",
            data={},
            metadata={"intent_capability": "presentation.table"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("t", "presentation.table"), capability="presentation.table"),
            ],
            params={"columns": ["Hello", "Value", "Change"]},
        )

        bind_skillir_to_nodes(draft, plan)

        node = draft.nodes["table"]
        self.assertEqual(node.data["columns"], ["Hello", "Value", "Change"])

    def test_does_not_override_existing_data_for_non_schema_keys(self):
        draft = GraphIRDraft()
        draft.params = {"columns": ["Hello"]}
        draft.add_node(GraphIRNode(
            id="table",
            type="AnalyticsTable",
            data={"custom_key": "preserve_me"},
            metadata={"intent_capability": "presentation.table"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("t", "presentation.table"), capability="presentation.table"),
            ],
            params={"columns": ["Hello"]},
        )

        bind_skillir_to_nodes(draft, plan)

        node = draft.nodes["table"]
        self.assertEqual(node.data["custom_key"], "preserve_me")

    def test_skillir_overrides_intent_params_for_schema_keys(self):
        draft = GraphIRDraft()
        draft.params = {"columns": ["Hello", "Value"]}
        draft.add_node(GraphIRNode(
            id="table",
            type="AnalyticsTable",
            data={"columns": ["Old_Column"]},
            metadata={"intent_capability": "presentation.table"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(
                    id=make_intent_id("t", "presentation.table"),
                    capability="presentation.table",
                    params={"columns": ["Old_Column"]},
                ),
            ],
            params={"columns": ["Hello", "Value"]},
        )

        bind_skillir_to_nodes(draft, plan)

        node = draft.nodes["table"]
        self.assertEqual(node.data["columns"], ["Hello", "Value"])

    def test_metrics_bound_to_kpi_node(self):
        draft = GraphIRDraft()
        draft.params = {"metrics": ["revenue", "growth"]}
        draft.add_node(GraphIRNode(
            id="kpi",
            type="KpiRow",
            data={},
            metadata={"intent_capability": "presentation.kpi_row"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("k", "presentation.kpi_row"), capability="presentation.kpi_row"),
            ],
            params={"metrics": ["revenue", "growth"]},
        )

        bind_skillir_to_nodes(draft, plan)

        node = draft.nodes["kpi"]
        self.assertEqual(node.data["metrics"], ["revenue", "growth"])

    def test_noop_when_plan_params_empty(self):
        draft = GraphIRDraft()
        draft.params = {}
        draft.add_node(GraphIRNode(
            id="table",
            type="AnalyticsTable",
            data={"columns": ["Existing"]},
            metadata={"intent_capability": "presentation.table"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("t", "presentation.table"), capability="presentation.table"),
            ],
            params={},
        )

        bind_skillir_to_nodes(draft, plan)

        node = draft.nodes["table"]
        self.assertEqual(node.data["columns"], ["Existing"])

    def test_noop_for_unknown_capability(self):
        draft = GraphIRDraft()
        draft.params = {"columns": ["Hello"]}
        draft.add_node(GraphIRNode(
            id="custom",
            type="CustomWidget",
            data={"some_key": "value"},
            metadata={"intent_capability": "custom.widget"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("c", "custom.widget"), capability="custom.widget"),
            ],
            params={"columns": ["Hello"]},
        )

        bind_skillir_to_nodes(draft, plan)

        node = draft.nodes["custom"]
        self.assertEqual(node.data["some_key"], "value")
        self.assertNotIn("columns", node.data)

    def test_merges_only_schema_matching_keys_from_plan_params(self):
        draft = GraphIRDraft()
        draft.params = {"columns": ["Hello"], "unrelated_key": "should_not_appear"}
        draft.add_node(GraphIRNode(
            id="table",
            type="AnalyticsTable",
            data={},
            metadata={"intent_capability": "presentation.table"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("t", "presentation.table"), capability="presentation.table"),
            ],
            params={"columns": ["Hello"], "unrelated_key": "should_not_appear"},
        )

        bind_skillir_to_nodes(draft, plan)

        node = draft.nodes["table"]
        self.assertEqual(node.data["columns"], ["Hello"])
        self.assertNotIn("unrelated_key", node.data)


class TestValidateBinding(unittest.TestCase):
    """validate_binding — fail fast on missing required keys."""

    def setUp(self):
        IntentExtensionRegistry.clear()

    def test_passes_when_required_columns_present(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(
            id="table",
            type="AnalyticsTable",
            data={"columns": ["Hello", "Value"]},
            metadata={"intent_capability": "presentation.table"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("t", "presentation.table"), capability="presentation.table"),
            ],
            params={"columns": ["Hello", "Value"]},
        )

        validate_binding(draft, plan)  # should not raise

    def test_passes_when_plan_params_empty(self):
        """Empty plan.params → no SkillIR contract → validation is a no-op."""
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(
            id="table",
            type="AnalyticsTable",
            data={},
            metadata={"intent_capability": "presentation.table"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("t", "presentation.table"), capability="presentation.table"),
            ],
            params={},
        )

        validate_binding(draft, plan)  # should not raise

    def test_raises_when_required_columns_missing_with_skillir(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(
            id="table",
            type="AnalyticsTable",
            data={},
            metadata={"intent_capability": "presentation.table"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("t", "presentation.table"), capability="presentation.table"),
            ],
            params={"some_other_key": "irrelevant"},
        )

        with self.assertRaises(SkillIRBindingError) as ctx:
            validate_binding(draft, plan)
        self.assertIn("columns", str(ctx.exception))

    def test_raises_when_metrics_empty_list_with_skillir(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(
            id="kpi",
            type="KpiRow",
            data={"metrics": []},
            metadata={"intent_capability": "presentation.kpi_row"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("k", "presentation.kpi_row"), capability="presentation.kpi_row"),
            ],
            params={"metrics": []},
        )

        with self.assertRaises(SkillIRBindingError):
            validate_binding(draft, plan)

    def test_skips_unknown_capability(self):
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(
            id="custom",
            type="CustomWidget",
            data={},
            metadata={"intent_capability": "custom.unknown"},
        ))
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("c", "custom.unknown"), capability="custom.unknown"),
            ],
            params={},
        )

        validate_binding(draft, plan)  # should not raise for unknown caps


class TestSkillIRInjectionEndToEnd(unittest.TestCase):
    """End-to-end: SkillIR params → GraphIR → JSX props in rendered output.

    This is the Skill IR Injection Test: verifies that columns, metrics, etc.
    from the LLM planner reach the generated JSX as actual props.
    """

    def setUp(self):
        IntentExtensionRegistry.clear()

    def _build_and_render(self, plan: IntentPlan) -> dict:
        from app.graphir.pipeline import GraphIRPipeline
        from app.graphir.backends import ReactBackend, BackendConfig
        graph, graph_layout = GraphIRPipeline.run(plan)
        backend = ReactBackend()
        config = BackendConfig(output_base_path="src/components/")
        fileops = backend.render(graph, graph_layout, config)
        return {op.path: op.content for op in fileops}

    def test_skillir_columns_appear_in_page_props(self):
        """The P0 test: SkillIR columns=["Hello","Value","Change"]
        MUST appear as JSX props in Page.tsx mount."""
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"),
                       capability="layout.page", params={}),
                Intent(id=make_intent_id("table", "presentation.table"),
                       capability="presentation.table", params={}),
            ],
            params={"columns": ["Hello", "Value", "Change"]},
        )

        files = self._build_and_render(plan)

        page = files.get("src/components/Page.tsx", "")
        # The binding layer should inject columns into node.data,
        # then _render_props_jsx produces columns={["Hello","Value","Change"]}
        self.assertIn(
            'columns={["Hello", "Value", "Change"]}',
            page,
            f"SkillIR columns not found in Page.tsx. Content:\n{page}",
        )

    def test_skillir_metrics_appear_in_page_props(self):
        """KPI metrics from SkillIR appear as JSX props in mount."""
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"),
                       capability="layout.page", params={}),
                Intent(id=make_intent_id("kpi", "presentation.kpi_row"),
                       capability="presentation.kpi_row", params={}),
            ],
            params={"metrics": ["revenue", "growth"]},
        )

        files = self._build_and_render(plan)

        page = files.get("src/components/Page.tsx", "")
        self.assertIn(
            'metrics={["revenue", "growth"]}',
            page,
            f"SkillIR metrics not found in Page.tsx. Content:\n{page}",
        )

    def test_both_columns_and_metrics_in_multi_intent_page(self):
        """Multiple intents with different SkillIR params all get bound."""
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"),
                       capability="layout.page", params={}),
                Intent(id=make_intent_id("table", "presentation.table"),
                       capability="presentation.table", params={}),
                Intent(id=make_intent_id("kpi", "presentation.kpi_row"),
                       capability="presentation.kpi_row", params={}),
            ],
            params={
                "columns": ["Product", "Revenue"],
                "metrics": ["total_revenue", "growth_rate"],
            },
        )

        files = self._build_and_render(plan)

        page = files.get("src/components/Page.tsx", "")
        self.assertIn('columns={["Product", "Revenue"]}', page)
        self.assertIn(
            'metrics={["total_revenue", "growth_rate"]}',
            page,
        )

    def test_skillir_overrides_decomposed_params(self):
        """SkillIR takes priority over intent.params for schema keys."""
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"),
                       capability="layout.page", params={}),
                Intent(id=make_intent_id("table", "presentation.table"),
                       capability="presentation.table",
                       params={"columns": ["Old_Hint"]}),
            ],
            params={"columns": ["Correct_Column"]},
        )

        files = self._build_and_render(plan)

        page = files.get("src/components/Page.tsx", "")
        self.assertIn(
            'columns={["Correct_Column"]}',
            page,
            f"SkillIR should override decomposed params. Content:\n{page}",
        )

    def test_empty_plan_params_preserves_existing_data(self):
        """No SkillIR params → existing intent.params preserved unchanged."""
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"),
                       capability="layout.page", params={}),
                Intent(id=make_intent_id("table", "presentation.table"),
                       capability="presentation.table",
                       params={"columns": ["Existing_Col"]}),
            ],
            params={},
        )

        files = self._build_and_render(plan)

        page = files.get("src/components/Page.tsx", "")
        self.assertIn(
            'columns={["Existing_Col"]}',
            page,
        )

    def test_skillir_with_existing_intent_params_merges_correctly(self):
        """Intent params and SkillIR params merge without collision."""
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"),
                       capability="layout.page", params={}),
                Intent(id=make_intent_id("table", "presentation.table"),
                       capability="presentation.table",
                       params={"table_data": [{"col1": "val1"}]}),
            ],
            params={"columns": ["Hello", "Value"]},
        )

        files = self._build_and_render(plan)

        page = files.get("src/components/Page.tsx", "")
        # SkillIR columns should be injected
        self.assertIn('columns={["Hello", "Value"]}', page)
        # Intent params (table_data) should be preserved
        self.assertIn("table_data", page)


class TestBuilderIntegration(unittest.TestCase):
    """Verify that builder correctly integrates binding step."""

    def setUp(self):
        IntentExtensionRegistry.clear()

    def test_builder_binds_skillir_params_into_node_data(self):
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"),
                       capability="layout.page", params={}),
                Intent(id=make_intent_id("table", "presentation.table"),
                       capability="presentation.table", params={}),
            ],
            params={"columns": ["Hello", "Value", "Change"]},
        )

        graph = GraphIRBuilder.build(plan)

        table_node = next(n for n in graph.nodes.values() if n.type == "AnalyticsTable")
        self.assertEqual(
            table_node.data.get("columns"),
            ["Hello", "Value", "Change"],
        )

    def test_builder_skips_validation_when_no_skillir(self):
        """Empty plan.params → no SkillIR → builder does not enforce schema."""
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"),
                       capability="layout.page", params={}),
                Intent(id=make_intent_id("table", "presentation.table"),
                       capability="presentation.table", params={}),
            ],
            params={},
        )
        graph = GraphIRBuilder.build(plan)
        self.assertIsInstance(graph, GraphIR)

    def test_builder_raises_on_missing_required_param(self):
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"),
                       capability="layout.page", params={}),
                Intent(id=make_intent_id("table", "presentation.table"),
                       capability="presentation.table", params={}),
            ],
            params={"some_irrelevant_key": "value"},
        )

        with self.assertRaises(SkillIRBindingError):
            GraphIRBuilder.build(plan)

    def test_builder_backward_compat_with_existing_params(self):
        """Existing tests with display.kpi_row and explicit params should still pass."""
        plan = IntentPlan(
            intents=[
                Intent(id=make_intent_id("page", "layout.page"),
                       capability="layout.page", params={}),
                Intent(id=make_intent_id("kpi", "display.kpi_row"),
                       capability="display.kpi_row",
                       params={"metrics": ["revenue", "growth"]}),
            ],
            params={"metrics": ["revenue", "growth"]},
        )

        graph = GraphIRBuilder.build(plan)

        kpi_node = next(n for n in graph.nodes.values() if n.type == "KpiRow")
        self.assertEqual(kpi_node.data["metrics"], ["revenue", "growth"])


if __name__ == "__main__":
    unittest.main()
