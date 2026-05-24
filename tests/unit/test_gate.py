"""Tests for Confidence Gate — pipeline gating based on semantic confidence.

Covers:
  - Gate passes for high-confidence frames
  - Gate blocks for low-confidence frames
  - Gate blocks for frames with no actions
  - Gate blocks for frames with no objects
  - Threshold customization
  - Gate result structure
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.semantic_frame import (
    StructuredSemanticFrame,
    ExtractedAction,
    ExtractedObject,
    ExtractedConstraint,
)
from app.engine.gate import (
    confidence_gate,
    GateResult,
    DEFAULT_CONFIDENCE_THRESHOLD,
)


def _make_high_conf_frame() -> StructuredSemanticFrame:
    return StructuredSemanticFrame(
        actions=[ExtractedAction(verb="modify", direct_object="table", confidence=0.9)],
        objects=[ExtractedObject(type="table", confidence=0.95)],
        constraints=[ExtractedConstraint(param="metrics", value=["revenue"], source="explicit", confidence=0.9)],
        confidence=0.72,
    )


def _make_low_conf_frame() -> StructuredSemanticFrame:
    return StructuredSemanticFrame(
        actions=[ExtractedAction(verb="add", direct_object="thing", confidence=0.3)],
        objects=[ExtractedObject(type="widget", confidence=0.4)],
        confidence=0.15,
    )


def _make_no_action_frame() -> StructuredSemanticFrame:
    return StructuredSemanticFrame(
        actions=[],
        objects=[ExtractedObject(type="table", confidence=0.8)],
        confidence=0.6,
    )


def _make_no_object_frame() -> StructuredSemanticFrame:
    return StructuredSemanticFrame(
        actions=[ExtractedAction(verb="modify", direct_object="x", confidence=0.8)],
        objects=[],
        confidence=0.6,
    )


class TestDefaultThreshold(unittest.TestCase):
    """DEFAULT_CONFIDENCE_THRESHOLD should be 0.4."""

    def test_default_threshold_value(self):
        self.assertEqual(DEFAULT_CONFIDENCE_THRESHOLD, 0.4)


class TestConfidenceGate(unittest.TestCase):
    """confidence_gate — main gating function."""

    def test_high_confidence_passes(self):
        result = confidence_gate(_make_high_conf_frame())
        self.assertFalse(result.blocked)

    def test_low_confidence_blocks(self):
        result = confidence_gate(_make_low_conf_frame())
        self.assertTrue(result.blocked)
        self.assertIn("confidence", result.reason.lower())

    def test_no_actions_blocks(self):
        result = confidence_gate(_make_no_action_frame())
        self.assertTrue(result.blocked)
        self.assertIn("action", result.reason.lower())

    def test_no_objects_blocks(self):
        result = confidence_gate(_make_no_object_frame())
        self.assertTrue(result.blocked)
        self.assertIn("object", result.reason.lower())

    def test_blocked_result_contains_reason(self):
        result = confidence_gate(_make_low_conf_frame())
        self.assertGreater(len(result.reason), 0)

    def test_blocked_result_contains_missing_info(self):
        frame = _make_low_conf_frame()
        frame.missing_info = ["unresolved_token"]
        result = confidence_gate(frame)
        self.assertIn("unresolved_token", result.missing_info)

    def test_blocked_result_contains_partial_frame(self):
        frame = _make_low_conf_frame()
        result = confidence_gate(frame)
        self.assertIsNotNone(result.partial_frame)
        self.assertIs(result.partial_frame, frame)

    def test_passed_result_no_reason(self):
        result = confidence_gate(_make_high_conf_frame())
        self.assertEqual(result.reason, "")

    def test_passed_result_no_partial_frame(self):
        result = confidence_gate(_make_high_conf_frame())
        self.assertIsNone(result.partial_frame)


class TestConfidenceGateThreshold(unittest.TestCase):
    """Custom threshold behavior."""

    def test_custom_threshold_blocks(self):
        frame = StructuredSemanticFrame(
            actions=[ExtractedAction(verb="modify", direct_object="x", confidence=0.9)],
            objects=[ExtractedObject(type="table", confidence=0.9)],
            confidence=0.5,
        )
        result = confidence_gate(frame, threshold=0.6)
        self.assertTrue(result.blocked)

    def test_custom_threshold_passes(self):
        frame = StructuredSemanticFrame(
            actions=[ExtractedAction(verb="modify", direct_object="x", confidence=0.9)],
            objects=[ExtractedObject(type="table", confidence=0.9)],
            confidence=0.5,
        )
        result = confidence_gate(frame, threshold=0.4)
        self.assertFalse(result.blocked)

    def test_zero_threshold_never_blocks(self):
        frame = StructuredSemanticFrame(confidence=0.0)
        result = confidence_gate(frame, threshold=0.0)
        # Should block because no actions/objects, not because of confidence
        self.assertTrue(result.blocked)

    def test_perfect_frame_always_passes(self):
        frame = StructuredSemanticFrame(
            actions=[ExtractedAction(verb="create", direct_object="dashboard", confidence=1.0)],
            objects=[ExtractedObject(type="dashboard", confidence=1.0)],
            confidence=1.0,
        )
        result = confidence_gate(frame, threshold=0.0)
        self.assertFalse(result.blocked)


class TestGateResultDataclass(unittest.TestCase):
    """GateResult structure."""

    def test_gate_result_has_all_fields(self):
        r = GateResult(blocked=True, reason="test", missing_info=["a"], partial_frame=None)
        self.assertTrue(r.blocked)
        self.assertEqual(r.reason, "test")
        self.assertEqual(r.missing_info, ["a"])

    def test_gate_result_defaults(self):
        r = GateResult(blocked=False)
        self.assertEqual(r.reason, "")
        self.assertEqual(r.missing_info, [])


class TestGateWithRealFrames(unittest.TestCase):
    """Gate behavior with frames built from real decomposition."""

    def test_run1_frame_passes_gate(self):
        from app.graphir.intent_decomposition import decompose_task
        from app.graphir.semantic_frame import build_frame_from_decomposition

        task = "Modify the table component by adding a tfoot to host buttons with actions."
        dec = decompose_task(task, use_embedding=False)
        frame = build_frame_from_decomposition(task, dec)
        result = confidence_gate(frame)
        self.assertFalse(result.blocked,
                         f"Run 1 should pass gate (confidence={frame.confidence:.3f})")

    def test_run2_frame_passes_gate(self):
        from app.graphir.intent_decomposition import decompose_task
        from app.graphir.semantic_frame import build_frame_from_decomposition

        task = ("Create a dashboard with KPI override to use net_revenue, "
                "monthly time configuration, retention analysis and grid layout")
        dec = decompose_task(task, use_embedding=False)
        frame = build_frame_from_decomposition(task, dec)
        result = confidence_gate(frame)
        self.assertFalse(result.blocked,
                         f"Run 2 should pass gate (confidence={frame.confidence:.3f})")

    def test_vague_task_blocked_by_gate(self):
        from app.graphir.intent_decomposition import decompose_task
        from app.graphir.semantic_frame import build_frame_from_decomposition

        task = "Haz algo con la cosa esa"
        dec = decompose_task(task, use_embedding=False)
        frame = build_frame_from_decomposition(task, dec)
        result = confidence_gate(frame)
        self.assertTrue(result.blocked,
                        f"Vague task should be blocked (confidence={frame.confidence:.3f})")


if __name__ == "__main__":
    unittest.main()
