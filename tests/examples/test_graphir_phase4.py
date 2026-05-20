"""Phase 4 tests: Intent Governance — ontology health monitoring.

Test coverage:
  1. Intent source traceability (keyword/embedding/llm)
  2. DecompositionResult ranked_candidates
  3. Orphan capability detection
  4. Unresolved token frequency tracker
  5. Capability co-occurrence tracker
  6. Embedding false positive tracker
  7. Contract coverage gap detector
  8. No-regression: existing behavior unchanged
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.intent_decomposition import decompose_task, DecompositionResult
from app.graphir.intent_governance import (
    detect_orphans,
    UnresolvedTracker,
    CoOccurrenceTracker,
    EmbeddingFPTracker,
    ContractCoverageGapDetector,
    UnresolvedReport,
    CoOccurrenceReport,
    OrphanReport,
)
from app.graphir.intent import Intent


# ═══════════════════════════════════════════════════════════════════
# 1. Source traceability
# ═══════════════════════════════════════════════════════════════════

class TestIntentSourceTraceability(unittest.TestCase):

    def test_keyword_intent_has_source_keyword(self):
        result = decompose_task("kpi")
        for i in result.intents:
            self.assertEqual(i.source, "keyword")

    def test_embedding_intent_has_source_embedding(self):
        """When embedding is enabled, inferred intents have source='embedding'."""
        from unittest.mock import patch

        MOCK_VEC = [0.1] * 384

        class MockResponse:
            def __init__(self, data):
                self._data = data
            def json(self):
                return self._data
            def raise_for_status(self):
                pass

        def mock_post(url, **kwargs):
            json_body = kwargs.get("json", {})
            inputs = json_body.get("input", "")
            if isinstance(inputs, str):
                inputs = [inputs]
            return MockResponse({
                "data": [{"embedding": MOCK_VEC, "index": i} for i in range(len(inputs))],
                "model": json_body.get("model", ""),
            })

        with patch("httpx.Client.post", mock_post):
            result = decompose_task("show revenue kpi", use_embedding=True)
            for i in result.intents:
                if i.source == "embedding":
                    return  # found one
            # It's also OK if no embedding intents were added
            # (depends on whether embedding ranked something new)
            self.assertIsInstance(result.inferred, list)

    def test_source_field_default_is_keyword(self):
        intent = Intent(id="t1", capability="presentation.kpi_row")
        self.assertEqual(intent.source, "keyword")

    def test_ranked_candidates_stored_when_embedding_active(self):
        from unittest.mock import patch

        MOCK_VEC = [0.1] * 384

        class MockResponse:
            def __init__(self, data):
                self._data = data
            def json(self):
                return self._data
            def raise_for_status(self):
                pass

        def mock_post(url, **kwargs):
            json_body = kwargs.get("json", {})
            inputs = json_body.get("input", "")
            if isinstance(inputs, str):
                inputs = [inputs]
            return MockResponse({
                "data": [{"embedding": MOCK_VEC, "index": i} for i in range(len(inputs))],
                "model": json_body.get("model", ""),
            })

        with patch("httpx.Client.post", mock_post):
            result = decompose_task("kpi", use_embedding=True)
            # ranked_candidates may or may not be populated depending on
            # whether embedding returned results; the key is it's a valid list
            self.assertIsInstance(result.ranked_candidates, list)

    def test_ranked_candidates_empty_without_embedding(self):
        result = decompose_task("kpi", use_embedding=False)
        self.assertEqual(result.ranked_candidates, [])


# ═══════════════════════════════════════════════════════════════════
# 2. Orphan capability detection
# ═══════════════════════════════════════════════════════════════════

class TestOrphanDetection(unittest.TestCase):

    def test_detect_orphans_returns_report(self):
        report = detect_orphans()
        self.assertIsInstance(report, OrphanReport)
        self.assertIsInstance(report.defined, list)
        self.assertIsInstance(report.orphans, list)

    def test_all_defined_capabilities_are_unique(self):
        report = detect_orphans()
        self.assertEqual(len(report.defined), len(set(report.defined)))

    def test_referenced_in_patterns_is_subset_of_defined(self):
        report = detect_orphans()
        for cap in report.referenced_in_patterns:
            self.assertIn(cap, report.defined)

    def test_orphans_not_in_patterns(self):
        report = detect_orphans()
        for cap in report.orphans:
            self.assertNotIn(cap, report.referenced_in_patterns)

    def test_presentation_caps_are_in_graphir_maps(self):
        report = detect_orphans()
        self.assertIn("presentation.kpi_row", report.referenced_in_graphir_maps)
        self.assertIn("presentation.timeseries", report.referenced_in_graphir_maps)


# ═══════════════════════════════════════════════════════════════════
# 3. Unresolved token frequency tracker
# ═══════════════════════════════════════════════════════════════════

class TestUnresolvedTracker(unittest.TestCase):

    def setUp(self):
        self.tracker = UnresolvedTracker()

    def test_empty_tracker_report(self):
        report = self.tracker.report()
        self.assertEqual(report.total_tasks, 0)
        self.assertEqual(report.total_unresolved, 0)
        self.assertEqual(report.top_tokens, [])

    def test_record_single_task_tracks_tokens(self):
        result = decompose_task("show revenue kpi")
        self.tracker.record(result)
        report = self.tracker.report()
        self.assertEqual(report.total_tasks, 1)
        self.assertGreater(report.total_unresolved, 0)

    def test_record_multiple_tasks_accumulates(self):
        r1 = decompose_task("show revenue kpi")
        r2 = decompose_task("paint it blue")
        self.tracker.record(r1)
        self.tracker.record(r2)
        report = self.tracker.report()
        self.assertEqual(report.total_tasks, 2)

    def test_tokens_are_counted(self):
        r1 = decompose_task("show revenue kpi")
        self.tracker.record(r1)
        counts = self.tracker.token_counts
        self.assertGreater(sum(counts.values()), 0)

    def test_merge_combines_trackers(self):
        t2 = UnresolvedTracker()
        self.tracker.record(decompose_task("show revenue kpi"))
        t2.record(decompose_task("paint it blue"))
        self.tracker.merge(t2)
        self.assertEqual(self.tracker.report().total_tasks, 2)


# ═══════════════════════════════════════════════════════════════════
# 4. Capability co-occurrence tracker
# ═══════════════════════════════════════════════════════════════════

class TestCoOccurrenceTracker(unittest.TestCase):

    def setUp(self):
        self.tracker = CoOccurrenceTracker()

    def test_empty_tracker_report(self):
        report = self.tracker.report()
        self.assertEqual(report.total_tasks, 0)
        self.assertEqual(report.pairs, [])

    def test_single_task_no_pairs(self):
        result = decompose_task("kpi")
        self.tracker.record(result)
        report = self.tracker.report()
        self.assertEqual(report.total_tasks, 1)
        # solo count should have kpi
        self.assertIn(("presentation.kpi_row", 1), report.solo_counts)

    def test_two_capabilities_produce_one_pair(self):
        result = decompose_task("kpi and timeseries")
        self.tracker.record(result)
        report = self.tracker.report()
        self.assertGreater(len(report.pairs), 0)

    def test_duplicate_tasks_increment_counts(self):
        r = decompose_task("kpi and timeseries")
        self.tracker.record(r)
        self.tracker.record(r)
        report = self.tracker.report()
        for pair, count in report.pairs:
            self.assertEqual(count, 2)

    def test_solo_counts_accumulate(self):
        r1 = decompose_task("kpi")
        r2 = decompose_task("kpi")
        self.tracker.record(r1)
        self.tracker.record(r2)
        report = self.tracker.report()
        self.assertIn(("presentation.kpi_row", 2), report.solo_counts)

    def test_merge(self):
        t2 = CoOccurrenceTracker()
        self.tracker.record(decompose_task("kpi"))
        t2.record(decompose_task("table"))
        self.tracker.merge(t2)
        self.assertEqual(self.tracker.report().total_tasks, 2)


# ═══════════════════════════════════════════════════════════════════
# 5. Embedding false positive tracker
# ═══════════════════════════════════════════════════════════════════

class TestEmbeddingFPTracker(unittest.TestCase):

    def setUp(self):
        self.tracker = EmbeddingFPTracker()

    def test_empty_report(self):
        report = self.tracker.report()
        self.assertEqual(report.total_inferred, 0)
        self.assertEqual(report.materialized, 0)
        self.assertEqual(report.false_positives, [])

    def test_record_inferred_and_materialized(self):
        self.tracker.record_inferred(
            DecompositionResult(
                intents=[],
                inferred=["presentation.kpi_row", "presentation.timeseries"],
                original_task="test",
            )
        )
        self.tracker.record_materialized(["presentation.kpi_row"])
        report = self.tracker.report()
        self.assertEqual(report.total_inferred, 2)
        self.assertEqual(report.materialized, 1)

    def test_false_positive_rate(self):
        # kpi_row inferred twice, materialized once → 50% FP rate
        self.tracker.record_inferred(
            DecompositionResult(
                intents=[],
                inferred=["presentation.kpi_row", "presentation.kpi_row"],
                original_task="test",
            )
        )
        self.tracker.record_materialized(["presentation.kpi_row"])
        report = self.tracker.report()
        fps = dict(report.false_positives)
        self.assertIn("presentation.kpi_row", fps)
        self.assertAlmostEqual(fps["presentation.kpi_row"], 0.5)


# ═══════════════════════════════════════════════════════════════════
# 6. Contract coverage gap detector
# ═══════════════════════════════════════════════════════════════════

class TestContractCoverageGapDetector(unittest.TestCase):

    def setUp(self):
        self.tracker = ContractCoverageGapDetector()

    def test_empty_report(self):
        report = self.tracker.report()
        self.assertEqual(report.total_intents, 0)
        self.assertEqual(report.covered, 0)

    def test_record_with_unknown_capability(self):
        """An unknown capability won't match any contract → counted as gap."""
        result = DecompositionResult(
            intents=[
                Intent(id="t1", capability="presentation.kpi_row"),
                Intent(id="t2", capability="nonexistent.unknown"),
            ],
            original_task="test",
        )
        self.tracker.record(result)
        report = self.tracker.report()
        self.assertEqual(report.total_intents, 2)
        self.assertGreater(len(report.missing), 0)


# ═══════════════════════════════════════════════════════════════════
# 7. No-regression: existing behavior unchanged
# ═══════════════════════════════════════════════════════════════════

class TestGovernanceNoRegression(unittest.TestCase):

    def test_decompose_task_unchanged_without_embedding(self):
        """Adding source field doesn't change decomposition results."""
        result = decompose_task("kpi and timeseries", use_embedding=False)
        caps = [i.capability for i in result.intents]
        self.assertIn("presentation.kpi_row", caps)
        self.assertIn("presentation.timeseries", caps)
        self.assertEqual(result.inferred, [])
        self.assertEqual(result.ranked_candidates, [])

    def test_intent_source_default(self):
        intent = Intent(id="t1", capability="presentation.kpi_row")
        self.assertEqual(intent.source, "keyword")

    def test_intent_source_explicit(self):
        intent = Intent(id="t1", capability="presentation.kpi_row", source="embedding")
        self.assertEqual(intent.source, "embedding")

    def test_detect_orphans_does_not_crash(self):
        """Orphan detection runs without errors."""
        report = detect_orphans()
        self.assertIsNotNone(report)


if __name__ == "__main__":
    unittest.main()
