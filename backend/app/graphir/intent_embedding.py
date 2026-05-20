"""Intent Embedding — lightweight embedding-based capability ranking.

Embeddings are used ONLY for ranking/retrieval, never for final decisions.
Keywords are the deterministic gatekeeper — embedding results populate the
`inferred` field of DecompositionResult with lower confidence.

Phase 3 design:
  - Keyword gatekeeper runs first (deterministic)
  - Embedding ranking runs on UNRESOLVED task tokens only
  - Top-k matches (not already detected) → inferred intents
  - Embedding never blocks, never decides, never overrides keywords
  - Configurable endpoint (OpenAI-compatible /v1/embeddings API)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from app.graphir.intent import CAPABILITY_REGISTRY, CapabilityDef

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────


def _capability_to_text(cap_id: str, defn: CapabilityDef) -> str:
    """Build a searchable text representation for a capability.

    Combines ID, description, axes, and category into a flat string
    suitable for embedding comparison.
    """
    parts = [cap_id, defn.description]
    ax = defn.axes
    for axis_name in ("presentation", "domain", "layout", "style"):
        val = getattr(ax, axis_name, None)
        if val:
            parts.append(val.replace(".", " "))
    if defn.parent:
        parts.append(defn.parent)
    return " | ".join(parts)


def _load_capability_texts() -> dict[str, str]:
    """Build a dict mapping capability ID → searchable text."""
    return {
        cid: _capability_to_text(cid, defn)
        for cid, defn in CAPABILITY_REGISTRY.items()
    }


# ── Cosine similarity (pure Python, no numpy) ─────────────────────


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Pure Python implementation — no external dependencies needed.
    Returns 0.0 on degenerate input.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(av * bv for av, bv in zip(a, b))
    norm_a = math.sqrt(sum(av * av for av in a))
    norm_b = math.sqrt(sum(bv * bv for bv in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Embedding API client ──────────────────────────────────────────


def _build_embedding_payload(text: str) -> dict[str, Any]:
    """Build the JSON body for an OpenAI-compatible /v1/embeddings request."""
    from app.config.settings import settings
    return {
        "input": text,
        "model": settings.EMBEDDING_MODEL,
    }


def compute_embedding(text: str) -> list[float] | None:
    """Compute an embedding vector for the given text.

    Calls an OpenAI-compatible /v1/embeddings API endpoint.
    Returns None on failure (caller should degrade gracefully).
    """
    import httpx
    from app.config.settings import settings

    url = f"{settings.resolved_embedding_base_url.rstrip('/')}/v1/embeddings"
    headers = {"Content-Type": "application/json"}
    api_key = settings.LLM_API_KEY
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        with httpx.Client(timeout=settings.EMBEDDING_TIMEOUT) as client:
            r = client.post(url, json=_build_embedding_payload(text), headers=headers)
            r.raise_for_status()
            data = r.json()
            return data["data"][0]["embedding"]
    except Exception as exc:
        logger.warning("Embedding API call failed: %s", exc)
        return None


def compute_embeddings_batch(texts: list[str]) -> list[list[float] | None]:
    """Compute embeddings for multiple texts in a single API call.

    Falls back to individual calls if batch fails.
    """
    if not texts:
        return []
    import httpx
    from app.config.settings import settings

    url = f"{settings.resolved_embedding_base_url.rstrip('/')}/v1/embeddings"
    headers = {"Content-Type": "application/json"}
    api_key = settings.LLM_API_KEY
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        with httpx.Client(timeout=settings.EMBEDDING_TIMEOUT) as client:
            r = client.post(
                url,
                json={
                    "input": texts,
                    "model": settings.EMBEDDING_MODEL,
                },
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            results: list[list[float] | None] = [None] * len(texts)
            for item in data["data"]:
                idx = item["index"]
                if 0 <= idx < len(texts):
                    results[idx] = item["embedding"]
            return results
    except Exception as exc:
        logger.warning("Batch embedding API call failed: %s", exc)
        return [compute_embedding(t) for t in texts]


# ── Embedding Index ──────────────────────────────────────────────


@dataclass
class EmbeddingIndex:
    """Pre-computed embedding vectors for all registered capabilities.

    Built lazily and cached. Each entry maps (capability_id, text, vector).
    """
    texts: dict[str, str] = field(default_factory=dict)
    vectors: dict[str, list[float]] = field(default_factory=dict)
    _built: bool = False

    def build(self, force: bool = False) -> None:
        """Fetch embeddings for all capabilities.

        Idempotent — only fetches once unless force=True.
        """
        if self._built and not force:
            return
        self.texts = _load_capability_texts()
        cap_ids = list(self.texts.keys())
        cap_texts = [self.texts[cid] for cid in cap_ids]
        batch = compute_embeddings_batch(cap_texts)
        self.vectors = {}
        for cid, vec in zip(cap_ids, batch):
            if vec is not None:
                self.vectors[cid] = vec
        self._built = True
        logger.info(
            "EmbeddingIndex built: %d/%d capabilities indexed",
            len(self.vectors), len(cap_ids),
        )

    def rank(
        self,
        query: str,
        top_k: int = 3,
        threshold: float = 0.0,
        exclude: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Rank capabilities by cosine similarity to query text.

        Args:
            query: The search query (task text or unresolved tokens).
            top_k: Maximum number of results.
            threshold: Minimum similarity score (0.0 = no filter).
            exclude: Capability IDs to exclude (e.g. already detected).

        Returns:
            List of (capability_id, score) sorted descending by score.
        """
        if not self._built:
            self.build()
        query_vec = compute_embedding(query)
        if query_vec is None:
            return []

        exclude_set = exclude or set()
        scored: list[tuple[str, float]] = []
        for cid, vec in self.vectors.items():
            if cid in exclude_set:
                continue
            score = cosine_similarity(query_vec, vec)
            if score >= threshold:
                scored.append((cid, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


# ── Module-level singleton ────────────────────────────────────────

_INDEX: EmbeddingIndex | None = None


def get_embedding_index() -> EmbeddingIndex:
    """Get or create the global embedding index singleton."""
    global _INDEX
    if _INDEX is None:
        _INDEX = EmbeddingIndex()
    return _INDEX


def reset_embedding_index() -> None:
    """Reset the global embedding index (useful for testing)."""
    global _INDEX
    _INDEX = None


def rank_capabilities(
    query: str,
    top_k: int | None = None,
    threshold: float | None = None,
    exclude: set[str] | None = None,
) -> list[tuple[str, float]]:
    """Convenience: one-shot capability ranking.

    Uses the global embedding index singleton.
    """
    from app.config.settings import settings
    index = get_embedding_index()
    return index.rank(
        query,
        top_k=top_k or settings.EMBEDDING_TOP_K,
        threshold=threshold if threshold is not None else settings.EMBEDDING_THRESHOLD,
        exclude=exclude,
    )
