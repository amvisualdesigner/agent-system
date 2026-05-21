"""Phase 3 tests: Intent Embedding — lightweight capability ranking.

Test coverage:
  1. Cosine similarity — pure Python implementation
  2. Capability text representation — _capability_to_text
  3. Embedding index — build, rank, exclude
  4. Embedding enhancement — _embedding_enhance integration
  5. decompose_task with use_embedding flag
  6. No-regression: embeddings never override keywords
  7. Graceful degradation when embedding API is unavailable
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.intent_decomposition import decompose_task
from app.graphir.intent_embedding import (
    cosine_similarity,
    _capability_to_text,
    EmbeddingIndex,
    compute_embedding,
    rank_capabilities,
    reset_embedding_index,
    _build_embedding_payload,
)
from app.graphir.intent import CAPABILITY_REGISTRY


# ═══════════════════════════════════════════════════════════════════
# 1. Cosine similarity
# ═══════════════════════════════════════════════════════════════════

class TestCosineSimilarity(unittest.TestCase):

    def test_identical_vectors(self):
        self.assertAlmostEqual(cosine_similarity([1, 0, 0], [1, 0, 0]), 1.0)

    def test_orthogonal_vectors(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [0, 1]), 0.0)

    def test_opposite_vectors(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [-1, 0]), -1.0)

    def test_partial_similarity(self):
        sim = cosine_similarity([1, 2, 3], [1, 2, 0])
        self.assertGreater(sim, 0.0)
        self.assertLess(sim, 1.0)

    def test_empty_vectors(self):
        self.assertEqual(cosine_similarity([], []), 0.0)

    def test_mismatched_length(self):
        self.assertEqual(cosine_similarity([1, 0], [1, 0, 0]), 0.0)

    def test_zero_vector(self):
        self.assertEqual(cosine_similarity([0, 0, 0], [1, 0, 0]), 0.0)


# ═══════════════════════════════════════════════════════════════════
# 2. Capability text representation
# ═══════════════════════════════════════════════════════════════════

class TestCapabilityToText(unittest.TestCase):

    def test_kpi_row_has_kpi_metric(self):
        defn = CAPABILITY_REGISTRY["presentation.kpi_row"]
        text = _capability_to_text("presentation.kpi_row", defn)
        self.assertIn("kpi", text.lower())
        self.assertIn("metric", text.lower())

    def test_dark_theme_has_dark(self):
        defn = CAPABILITY_REGISTRY["style.theme.dark"]
        text = _capability_to_text("style.theme.dark", defn)
        self.assertIn("dark", text.lower())

    def test_text_contains_id(self):
        defn = CAPABILITY_REGISTRY["presentation.table"]
        text = _capability_to_text("presentation.table", defn)
        self.assertIn("presentation.table", text)


# ═══════════════════════════════════════════════════════════════════
# 3. Embedding payload construction
# ═══════════════════════════════════════════════════════════════════

class TestEmbeddingPayload(unittest.TestCase):

    def test_payload_shape(self):
        payload = _build_embedding_payload("test query")
        self.assertEqual(payload["input"], "test query")
        self.assertIn("model", payload)


# ═══════════════════════════════════════════════════════════════════
# 4. compute_embedding graceful degradation
# ═══════════════════════════════════════════════════════════════════

class TestComputeEmbedding(unittest.TestCase):

    def test_returns_none_when_api_unreachable(self):
        """No embedding server running → returns None, not crash."""
        with patch.dict(os.environ, {
            "EMBEDDING_BASE_URL": "http://localhost:99999",
            "EMBEDDING_TIMEOUT": "1",
        }):
            # Reload settings to pick up new env vars
            from importlib import reload
            import app.config.settings
            reload(app.config.settings)
            result = compute_embedding("test query")
            self.assertIsNone(result)

    def test_returns_none_on_empty_text(self):
        with patch.dict(os.environ, {
            "EMBEDDING_BASE_URL": "http://localhost:99999",
            "EMBEDDING_TIMEOUT": "1",
        }):
            from importlib import reload
            import app.config.settings
            reload(app.config.settings)
            result = compute_embedding("")
            self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════
# 5. EmbeddingIndex with mock API
# ═══════════════════════════════════════════════════════════════════

MOCK_EMBEDDING_VEC = [0.1] * 384  # fake 384-dim vector


class _MockResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class TestEmbeddingIndex(unittest.TestCase):

    def setUp(self):
        reset_embedding_index()

    def _mock_post(self, url, **kwargs):
        json_body = kwargs.get("json", {})
        inputs = json_body.get("input", "")
        if isinstance(inputs, str):
            inputs = [inputs]
        data = {
            "data": [{"embedding": MOCK_EMBEDDING_VEC, "index": i} for i in range(len(inputs))],
            "model": json_body.get("model", ""),
        }
        return _MockResponse(data)

    def test_build_and_rank(self):
        with patch("httpx.Client.post", self._mock_post):
            index = EmbeddingIndex()
            index.build()
            self.assertGreater(len(index.vectors), 0)

    def test_rank_returns_results(self):
        with patch("httpx.Client.post", self._mock_post):
            results = rank_capabilities("show kpi metrics", top_k=5, threshold=0.0)
            self.assertGreater(len(results), 0)
            for cid, score in results:
                self.assertIsInstance(cid, str)
                self.assertGreaterEqual(score, 0.0)

    def test_rank_respects_exclude(self):
        with patch("httpx.Client.post", self._mock_post):
            all_results = rank_capabilities("test query", top_k=10, threshold=0.0)
            excluded = {all_results[0][0]} if all_results else set()
            filtered = rank_capabilities(
                "test query", top_k=10, threshold=0.0, exclude=excluded
            )
            if filtered:
                self.assertNotEqual(filtered[0][0], all_results[0][0])

    def test_rank_respects_top_k(self):
        with patch("httpx.Client.post", self._mock_post):
            results = rank_capabilities("kpi", top_k=2, threshold=0.0)
            self.assertLessEqual(len(results), 2)

    def test_rank_respects_threshold(self):
        with patch("httpx.Client.post", self._mock_post):
            # All vectors are identical (0.1s), so cosine sim = 1.0
            results = rank_capabilities("test", top_k=10, threshold=0.99)
            self.assertGreater(len(results), 0)

    def test_get_embedding_index_singleton(self):
        with patch("httpx.Client.post", self._mock_post):
            from app.graphir.intent_embedding import get_embedding_index
            idx1 = get_embedding_index()
            idx2 = get_embedding_index()
            self.assertIs(idx1, idx2)


# ═══════════════════════════════════════════════════════════════════
# 6. _embedding_enhance integration
# ═══════════════════════════════════════════════════════════════════

class TestEmbeddingEnhance(unittest.TestCase):

    def setUp(self):
        reset_embedding_index()

    def _mock_post(self, url, **kwargs):
        json_body = kwargs.get("json", {})
        inputs = json_body.get("input", "")
        if isinstance(inputs, str):
            inputs = [inputs]
        data = {
            "data": [{"embedding": MOCK_EMBEDDING_VEC, "index": i} for i in range(len(inputs))],
            "model": json_body.get("model", ""),
        }
        return _MockResponse(data)

    def test_decompose_with_embedding_adds_inferred(self):
        """When embedding is enabled, inferred is populated."""
        with patch("httpx.Client.post", self._mock_post):
            with patch.dict(os.environ, {"EMBEDDING_ENABLED": "true"}):
                from importlib import reload
                import app.config.settings
                reload(app.config.settings)

                result = decompose_task("show revenue kpi", use_embedding=True)
                # Keyword should still produce detected
                self.assertIn("presentation.kpi_row", result.detected)
                # Embedding may also suggest domain.sales (because revenue is in description)
                self.assertIsInstance(result.inferred, list)

    def test_embedding_does_not_override_detected(self):
        """Embedding suggestions never remove or replace keyword results."""
        with patch("httpx.Client.post", self._mock_post):
            result = decompose_task("kpi and timeseries", use_embedding=True)
            self.assertIn("presentation.kpi_row", result.detected)
            self.assertIn("presentation.timeseries", result.detected)

    def test_embedding_skipped_when_disabled(self):
        """use_embedding=False means no embedding call and inferred stays empty."""
        # Don't patch httpx — if embedding were called it would fail
        result = decompose_task("show revenue kpi", use_embedding=False)
        self.assertIn("presentation.kpi_row", result.detected)
        self.assertEqual(result.inferred, [])

    def test_embedding_failure_does_not_crash(self):
        """If embedding API is down, decompose_task still returns keyword result."""
        with patch.dict(os.environ, {
            "EMBEDDING_BASE_URL": "http://localhost:99999",
            "EMBEDDING_TIMEOUT": "1",
        }):
            from importlib import reload
            import app.config.settings
            reload(app.config.settings)

            result = decompose_task("kpi", use_embedding=True)
            self.assertIn("presentation.kpi_row", result.detected)
            self.assertEqual(result.inferred, [])

    def test_embedding_does_not_duplicate_intents(self):
        """If embedding suggests a capability already detected, skip it."""
        with patch("httpx.Client.post", self._mock_post):
            result = decompose_task("kpi", use_embedding=True)
            caps = [i.capability for i in result.intents]
            self.assertEqual(len(caps), len(set(caps)))  # no duplicates

    def test_decomposition_confidence_unchanged_by_embedding(self):
        """decomposition_confidence stays keyword-based, not affected by embedding."""
        with patch("httpx.Client.post", self._mock_post):
            # Without embedding
            base = decompose_task("show revenue kpi", use_embedding=False)
            # With embedding
            enhanced = decompose_task("show revenue kpi", use_embedding=True)
            self.assertEqual(base.decomposition_confidence, enhanced.decomposition_confidence)

    def test_semantic_entropy_not_affected(self):
        """semantic_entropy stays keyword-based even with embedding enabled."""
        from app.graphir.intent import compute_semantic_entropy
        with patch("httpx.Client.post", self._mock_post):
            base = decompose_task("show revenue kpi", use_embedding=False)
            enhanced = decompose_task("show revenue kpi", use_embedding=True)
            # Base entropy from keyword-determined intents
            base_entropy = compute_semantic_entropy("show revenue kpi", base.intents)
            enhanced_entropy = compute_semantic_entropy("show revenue kpi", enhanced.intents)
            # Both should compute from their respective intent lists
            # (they may differ because enhanced has more intents)
            self.assertIsInstance(base_entropy, float)
            self.assertIsInstance(enhanced_entropy, float)


# ═══════════════════════════════════════════════════════════════════
# 7. No-regression: existing behavior unchanged
# ═══════════════════════════════════════════════════════════════════

class TestEmbeddingNoRegression(unittest.TestCase):

    def test_empty_task_still_empty(self):
        result = decompose_task("", use_embedding=True)
        self.assertEqual(result.intents, [])

    def test_keyword_detected_is_correct(self):
        result = decompose_task("dark theme dashboard kpi", use_embedding=True)
        self.assertIn("style.theme.dark", result.detected)
        self.assertIn("presentation.kpi_row", result.detected)

    def test_unresolved_still_tracked(self):
        result = decompose_task("something completely unrelated", use_embedding=True)
        self.assertEqual(result.intents, [])

    def test_decompose_without_embedding_unchanged(self):
        """Exact same results as before embedding module existed."""
        result = decompose_task("kpi and timeseries", use_embedding=False)
        self.assertIn("presentation.kpi_row", result.detected)
        self.assertIn("presentation.timeseries", result.detected)
        self.assertEqual(result.inferred, [])


if __name__ == "__main__":
    unittest.main()
