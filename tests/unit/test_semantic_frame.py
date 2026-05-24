"""Tests for StructuredSemanticFrame — intent decomposition → semantic frame.

Covers Phase 0 SPIKE scenarios from SEMANTIC_CONTRACT_SUBGRAPH_PLAN.md:
  - Run 1: "Modify the table component by adding a tfoot to host buttons with actions."
  - Run 2: Dashboard + KPI override + retention + monthly + grid
  - Edge cases: empty task, vague input, no actions, no objects
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.intent import Intent, make_intent_id
from app.graphir.intent_decomposition import (
    DecompositionResult,
    _keyword_decompose,
    decompose_task,
)
from app.graphir.semantic_frame import (
    build_frame_from_decomposition,
    StructuredSemanticFrame,
    ExtractedAction,
    ExtractedObject,
    ExtractedConstraint,
    _extract_actions,
    _extract_objects,
    _extract_constraints,
    _compute_frame_confidence,
)


def _make_decomposition_result(
    intents: list[Intent],
    confidence: float = 1.0,
    detected: list[str] | None = None,
    unresolved: list[str] | None = None,
    task: str = "",
) -> DecompositionResult:
    return DecompositionResult(
        intents=intents,
        decomposition_confidence=confidence,
        detected=detected or [],
        unresolved=unresolved or [],
        original_task=task,
    )


class TestExtractActions(unittest.TestCase):
    """_extract_actions: raw task text → list[ExtractedAction]."""

    def test_detect_add(self):
        actions = _extract_actions("add a tfoot to the table")
        verbs = [a.verb for a in actions]
        self.assertIn("add", verbs)

    def test_detect_modify(self):
        actions = _extract_actions("modify the table component")
        verbs = [a.verb for a in actions]
        self.assertIn("modify", verbs)

    def test_detect_create(self):
        actions = _extract_actions("create a dashboard")
        verbs = [a.verb for a in actions]
        self.assertIn("create", verbs)

    def test_detect_override(self):
        actions = _extract_actions("override so that KPI uses net_revenue")
        verbs = [a.verb for a in actions]
        self.assertIn("override", verbs)

    def test_detect_remove(self):
        actions = _extract_actions("remove the chart widget")
        verbs = [a.verb for a in actions]
        self.assertIn("remove", verbs)

    def test_detect_show(self):
        actions = _extract_actions("show retention analysis")
        verbs = [a.verb for a in actions]
        self.assertIn("show", verbs)

    def test_detect_apply(self):
        actions = _extract_actions("apply monthly time configuration")
        verbs = [a.verb for a in actions]
        self.assertIn("apply", verbs)

    def test_no_action_for_vague_text(self):
        actions = _extract_actions("haz algo con la cosa esa")
        self.assertEqual(len(actions), 0)

    def test_multiple_actions_detected(self):
        actions = _extract_actions("modify the table and add a tfoot")
        verbs = [a.verb for a in actions]
        self.assertIn("modify", verbs)
        self.assertIn("add", verbs)

    def test_direct_object_extracted(self):
        actions = _extract_actions("modify table")
        found = [a for a in actions if a.verb == "modify"]
        if found:
            self.assertEqual(found[0].direct_object, "table")


class TestExtractObjects(unittest.TestCase):
    """_extract_objects: task text + intents → list[ExtractedObject]."""

    def test_detect_table(self):
        intents = [Intent(id=make_intent_id("t", "presentation.table"), capability="presentation.table")]
        objs = _extract_objects("show me the analytics table", intents)
        types = [o.type for o in objs]
        self.assertIn("table", types)

    def test_detect_kpi(self):
        objs = _extract_objects("show kpi metrics", [])
        types = [o.type for o in objs]
        self.assertIn("kpi_row", types)

    def test_detect_button(self):
        objs = _extract_objects("add buttons to the table", [])
        types = [o.type for o in objs]
        self.assertIn("button", types)

    def test_detect_tfoot(self):
        objs = _extract_objects("add a tfoot", [])
        types = [o.type for o in objs]
        self.assertIn("tfoot", types)

    def test_detect_retention(self):
        objs = _extract_objects("retention analysis", [])
        types = [o.type for o in objs]
        self.assertIn("retention_widget", types)

    def test_detect_grid(self):
        objs = _extract_objects("grid layout", [])
        types = [o.type for o in objs]
        self.assertIn("grid", types)

    def test_detect_dashboard(self):
        objs = _extract_objects("create a dashboard", [])
        types = [o.type for o in objs]
        self.assertIn("dashboard", types)

    def test_no_duplicates(self):
        objs = _extract_objects("table and analytics table", [])
        table_count = sum(1 for o in objs if o.type == "table")
        self.assertEqual(table_count, 1, "table should not be duplicated")


class TestExtractConstraints(unittest.TestCase):
    """_extract_constraints: task text → list[ExtractedConstraint]."""

    def test_override_metric(self):
        constraints = _extract_constraints("use net_revenue instead of revenue", "use net_revenue instead of revenue")
        metrics = [c for c in constraints if c.param == "metrics"]
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0].value, ["net_revenue"])
        self.assertEqual(metrics[0].source, "explicit")

    def test_override_variant(self):
        constraints = _extract_constraints("override so that KPI uses net_revenue", "override so that kpi uses net_revenue")
        metrics = [c for c in constraints if c.param == "metrics"]
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0].value, ["net_revenue"])

    def test_monthly_granularity(self):
        constraints = _extract_constraints("monthly time configuration", "monthly time configuration")
        time_c = [c for c in constraints if c.param == "time_granularity"]
        self.assertEqual(len(time_c), 1)
        self.assertEqual(time_c[0].value, "monthly")

    def test_top_k(self):
        constraints = _extract_constraints("top 5 customers", "top 5 customers")
        top = [c for c in constraints if c.param == "top_k"]
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0].value, 5)

    def test_known_metrics(self):
        constraints = _extract_constraints("show revenue and growth", "show revenue and growth")
        metrics = [c for c in constraints if c.param == "mentioned_metrics"]
        self.assertEqual(len(metrics), 1)
        self.assertIn("revenue", metrics[0].value)
        self.assertIn("growth", metrics[0].value)

    def test_no_constraints_for_vague(self):
        constraints = _extract_constraints("haz algo", "haz algo")
        self.assertEqual(len(constraints), 0)


class TestComputeFrameConfidence(unittest.TestCase):
    """_compute_frame_confidence: weighted formula."""

    def test_high_confidence(self):
        actions = [ExtractedAction(verb="modify", direct_object="table", confidence=0.9)]
        objects = [ExtractedObject(type="table", confidence=0.95)]
        constraints = [ExtractedConstraint(param="metrics", value=["net_revenue"], source="explicit", confidence=0.95)]
        conf = _compute_frame_confidence(actions, objects, constraints, [])
        self.assertGreater(conf, 0.5)

    def test_low_confidence_no_actions(self):
        conf = _compute_frame_confidence([], [ExtractedObject(type="table", confidence=0.8)], [], [])
        # 0.0 (actions) + 0.8*0.35 (objects) + 0.0 (constraints) = 0.28
        self.assertAlmostEqual(conf, 0.28, places=4)

    def test_missing_penalty(self):
        actions = [ExtractedAction(verb="add", direct_object="x", confidence=0.9)]
        objects = [ExtractedObject(type="table", confidence=0.9)]
        no_missing = _compute_frame_confidence(actions, objects, [], [])
        with_missing = _compute_frame_confidence(actions, objects, [], ["foo", "bar", "baz"])
        self.assertGreater(no_missing, with_missing)

    def test_confidence_bounded(self):
        actions = [ExtractedAction(verb="modify", direct_object="x", confidence=1.0)]
        objects = [ExtractedObject(type="table", confidence=1.0)]
        constraints = [ExtractedConstraint(param="x", value="y", source="explicit", confidence=1.0)]
        conf = _compute_frame_confidence(actions, objects, constraints, [])
        self.assertLessEqual(conf, 1.0)
        self.assertGreaterEqual(conf, 0.0)


class TestBuildFrameFromDecomposition(unittest.TestCase):
    """build_frame_from_decomposition: the main entry point."""

    def test_empty_task_does_not_crash(self):
        frame = build_frame_from_decomposition("", _make_decomposition_result([]))
        self.assertIsInstance(frame, StructuredSemanticFrame)

    def test_none_task_does_not_crash(self):
        frame = build_frame_from_decomposition("", _make_decomposition_result([], task=""))
        self.assertIsInstance(frame, StructuredSemanticFrame)

    def test_single_intent_produces_frame(self):
        intents = [Intent(
            id=make_intent_id("test", "presentation.table"),
            capability="presentation.table",
        )]
        dec = _make_decomposition_result(intents, task="show table")
        frame = build_frame_from_decomposition("show table", dec)
        self.assertIsInstance(frame, StructuredSemanticFrame)
        self.assertGreaterEqual(len(frame.objects), 1)

    def test_vague_task_low_confidence(self):
        frame = build_frame_from_decomposition("haz algo con la cosa esa", _make_decomposition_result([], task="haz algo con la cosa esa"))
        self.assertLess(frame.confidence, 0.3)

    # ── Run 1 from the plan ──

    def test_run1_tfoot_buttons(self):
        task = "Modify the table component by adding a tfoot to host buttons with actions."
        dec = decompose_task(task, use_embedding=False)
        frame = build_frame_from_decomposition(task, dec)

        self.assertGreaterEqual(len(frame.actions), 1,
                                "Run 1 should have at least one action")
        self.assertGreaterEqual(len(frame.objects), 1,
                                "Run 1 should have at least one object")
        # Confidence should be above 0 (the plan expects ~0.72)
        self.assertGreater(frame.confidence, 0.0,
                           "Run 1 confidence should be > 0.0")

    def test_run1_action_verbs(self):
        task = "Modify the table component by adding a tfoot to host buttons with actions."
        dec = decompose_task(task, use_embedding=False)
        frame = build_frame_from_decomposition(task, dec)
        verbs = {a.verb for a in frame.actions}
        self.assertIn("modify", verbs,
                      "Run 1 should detect 'modify' action")
        self.assertIn("add", verbs,
                      "Run 1 should detect 'add' action")

    def test_run1_objects(self):
        task = "Modify the table component by adding a tfoot to host buttons with actions."
        dec = decompose_task(task, use_embedding=False)
        frame = build_frame_from_decomposition(task, dec)
        types = {o.type for o in frame.objects}
        self.assertIn("table", types,
                      "Run 1 should detect table object")
        self.assertIn("tfoot", types,
                      "Run 1 should detect tfoot object")
        self.assertIn("button", types,
                      "Run 1 should detect button object")

    # ── Run 2 from the plan ──

    def test_run2_dashboard_override(self):
        task = ("Create a dashboard with KPI override to use net_revenue, "
                "monthly time configuration, retention analysis and grid layout")
        dec = decompose_task(task, use_embedding=False)
        frame = build_frame_from_decomposition(task, dec)

        self.assertGreaterEqual(len(frame.actions), 1,
                                "Run 2 should have at least one action")
        self.assertGreaterEqual(len(frame.objects), 1,
                                "Run 2 should have at least one object")
        self.assertGreater(frame.confidence, 0.0,
                           "Run 2 confidence should be > 0.0")

    def test_run2_action_verbs(self):
        task = ("Create a dashboard with KPI override to use net_revenue, "
                "monthly time configuration, retention analysis and grid layout")
        dec = decompose_task(task, use_embedding=False)
        frame = build_frame_from_decomposition(task, dec)
        verbs = {a.verb for a in frame.actions}
        self.assertIn("create", verbs, "Run 2 should detect 'create' action")
        self.assertIn("override", verbs, "Run 2 should detect 'override' action")

    def test_run2_objects(self):
        task = ("Create a dashboard with KPI override to use net_revenue, "
                "monthly time configuration, retention analysis and grid layout")
        dec = decompose_task(task, use_embedding=False)
        frame = build_frame_from_decomposition(task, dec)
        types = {o.type for o in frame.objects}
        self.assertIn("dashboard", types,
                      "Run 2 should detect dashboard object")
        self.assertIn("kpi_row", types,
                      "Run 2 should detect kpi_row object")
        self.assertIn("retention_widget", types,
                      "Run 2 should detect retention object")
        self.assertIn("grid", types,
                      "Run 2 should detect grid object")

    def test_run2_constraints(self):
        task = ("Create a dashboard with KPI override to use net_revenue, "
                "monthly time configuration, retention analysis and grid layout")
        dec = decompose_task(task, use_embedding=False)
        frame = build_frame_from_decomposition(task, dec)
        params = {c.param for c in frame.constraints}
        self.assertIn("metrics", params,
                      "Run 2 should detect metrics constraint")
        self.assertIn("time_granularity", params,
                      "Run 2 should detect time_granularity constraint")
        # "ratio" should NOT appear in the frame fields — only in raw_decomposition
        frame_dict = {
            "actions": [(a.verb, a.direct_object) for a in frame.actions],
            "objects": [o.type for o in frame.objects],
            "constraints": [(c.param, c.value) for c in frame.constraints],
            "missing_info": frame.missing_info,
        }
        frame_str = str(frame_dict).lower()
        self.assertNotIn("ratio", frame_str,
                         "Run 2 frame fields should not contain 'ratio'")

    def test_run2_confidence_above_threshold(self):
        task = ("Create a dashboard with KPI override to use net_revenue, "
                "monthly time configuration, retention analysis and grid layout")
        dec = decompose_task(task, use_embedding=False)
        frame = build_frame_from_decomposition(task, dec)
        self.assertGreater(frame.confidence, 0.4,
                           "Run 2 confidence should pass the gate (>= 0.4)")


class TestBuildFrameEdgeCases(unittest.TestCase):
    """Edge cases for frame builder."""

    def test_keyword_task_passes(self):
        frame = build_frame_from_decomposition(
            "add a button",
            _make_decomposition_result(
                [Intent(id=make_intent_id("b", "presentation.table"), capability="presentation.table")],
                task="add a button",
            ),
        )
        self.assertIsInstance(frame, StructuredSemanticFrame)

    def test_decomposition_result_without_intents(self):
        frame = build_frame_from_decomposition(
            "",
            _make_decomposition_result([]),
        )
        self.assertEqual(len(frame.actions), 0)

    def test_missing_info_for_novel_tokens(self):
        frame = build_frame_from_decomposition(
            "flibbertygibbet the whatzit",
            _make_decomposition_result([], task="flibbertygibbet the whatzit"),
        )
        self.assertGreaterEqual(len(frame.missing_info), 1)

    def test_raw_decomposition_preserved(self):
        dec = _make_decomposition_result([], task="test")
        frame = build_frame_from_decomposition("test", dec)
        self.assertIs(frame.raw_decomposition, dec,
                      "raw_decomposition should reference the original DecompositionResult")


if __name__ == "__main__":
    unittest.main()
