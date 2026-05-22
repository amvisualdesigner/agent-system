"""IdentityResolver — deterministic decision kernel.

Pure Core: zero IO, 100% deterministic.

Takes ranked candidates from IntentFileMatcher and applies the
IDENTITY_SPEC rules to produce final FileOpDecisions.

Decision hierarchy (per IDENTITY_SPEC.md §4):
  1. CanonicalIdentity.fingerprint() → check resolved_mapping
     → FOUND: UPDATE on mapped file (confidence 1.0)
  2. CanonicalIdentity.fingerprint() → check file_nodes[].canonical_ids
     → FOUND: UPDATE on pre-existing file (confidence 0.95)
  2.5 Component name → check file_nodes[].component_names (Phase 6a)
     → FOUND: UPDATE on containing file (confidence 0.85)
  3. Best candidate score → threshold comparison
     → score >= UPDATE_THRESHOLD: UPDATE
     → score >= EXTEND_THRESHOLD: EXTEND
     → score <  EXTEND_THRESHOLD: CREATE

Phase 2 hook: resolved_mapping is loaded from RepositorySemanticMemory
and passed into the constructor. This is the only interface between
semantic memory and the decision kernel.

Phase 6a: resolved_mapping is dict[fingerprint, MemoryRecord].
"""

from __future__ import annotations

import logging

from app.graphir.constraint.models import Decision, FileOpDecision, MemoryRecord
from app.graphir.constraint.identity import CanonicalIdentity

logger = logging.getLogger(__name__)


class IdentityResolver:
    """Pure Core: resolves candidates into decisions per IDENTITY_SPEC rules.

    Args:
        resolved_mapping: dict[fingerprint, MemoryRecord] from semantic memory
            (Phase 6a+). Empty in Phase 1 (all decisions via scoring).
    """

    UPDATE_THRESHOLD = 0.55
    EXTEND_THRESHOLD = 0.35

    def __init__(self, resolved_mapping: dict[str, MemoryRecord] | None = None):
        self.resolved_mapping = resolved_mapping or {}

    def resolve(
        self,
        identities: dict[str, CanonicalIdentity],
        candidates: dict[str, list[tuple[float, object]]],
        file_nodes: dict[str, object],
    ) -> dict[str, FileOpDecision]:
        """Resolve ranked candidates into final decisions.

        Args:
            identities: dict[graphir_node_id, CanonicalIdentity]
                from IntentFileMatcher.match()
            candidates: dict[graphir_node_id, list[(score, FileNode)]]
                from IntentFileMatcher.match()
            file_nodes: dict[rel_path, FileNode] from RepositoryIndexer

        Returns:
            dict[graphir_node_id, FileOpDecision]
        """
        decisions: dict[str, FileOpDecision] = {}

        for node_id in identities:
            identity = identities[node_id]
            decision = self._decide(identity, node_id, candidates, file_nodes)
            decisions[node_id] = decision

        return decisions

    def _decide(
        self,
        identity: CanonicalIdentity,
        node_id: str,
        candidates: dict[str, list[tuple[float, object]]],
        file_nodes: dict[str, object],
    ) -> FileOpDecision:
        fp = identity.fingerprint()
        node_candidates = candidates.get(node_id, [])

        # ── Level 1: Check resolved mapping (from semantic memory) ──
        if fp in self.resolved_mapping:
            rec = self.resolved_mapping[fp]
            target = rec.file_path
            if target in file_nodes:
                return FileOpDecision(
                    intent_id=identity.capability_id,
                    graphir_node_id=node_id,
                    decision=Decision.UPDATE,
                    target_file=target,
                    confidence=1.0,
                    rationale=f"Identity match via resolved mapping: {fp} → {target}",
                )

        # ── Level 2: Check file index for known identity ──
        for fn in file_nodes.values():
            if fp in getattr(fn, "canonical_ids", []):
                return FileOpDecision(
                    intent_id=identity.capability_id,
                    graphir_node_id=node_id,
                    decision=Decision.UPDATE,
                    target_file=fn.path,
                    confidence=0.95,
                    rationale=f"Identity match in file index: {fp} → {fn.path}",
                )

        # ── Level 2.5 (Phase 6a): Existence-aware check ──
        # Prevents duplicate CREATE when component_name exists in a file
        # but canonical fingerprint didn't match via Levels 1 or 2.
        component_name = identity.component_name
        for fn in file_nodes.values():
            if component_name in getattr(fn, "component_names", []):
                return FileOpDecision(
                    intent_id=identity.capability_id,
                    graphir_node_id=node_id,
                    decision=Decision.UPDATE,
                    target_file=fn.path,
                    confidence=0.85,
                    rationale=f"Level 2.5: '{component_name}' exists in {fn.path}",
                )

        # ── Level 3: Similarity fallback ──
        return self._score_to_decision(identity, node_id, node_candidates)

    def _score_to_decision(
        self,
        identity: CanonicalIdentity,
        node_id: str,
        candidates: list[tuple[float, object]],
    ) -> FileOpDecision:
        if not candidates:
            return FileOpDecision(
                intent_id=identity.capability_id,
                graphir_node_id=node_id,
                decision=Decision.CREATE,
                target_file=self._default_path(identity),
                confidence=0.0,
                rationale="No files in workspace — CREATE",
            )

        # Defensive sort: highest score first
        sorted_candidates = sorted(candidates, key=lambda x: -x[0])
        best_score, best_fn = sorted_candidates[0]

        if best_score < self.EXTEND_THRESHOLD:
            return FileOpDecision(
                intent_id=identity.capability_id,
                graphir_node_id=node_id,
                decision=Decision.CREATE,
                target_file=self._default_path(identity),
                confidence=best_score,
                rationale=f"No matching file found (best={best_score:.2f})",
            )

        if best_score >= self.UPDATE_THRESHOLD:
            decision = Decision.UPDATE
            rationale = f"Similarity score {best_score:.2f}: UPDATE on {best_fn.path}"
        else:
            decision = Decision.EXTEND
            rationale = f"Similarity score {best_score:.2f}: EXTEND on {best_fn.path}"

        return FileOpDecision(
            intent_id=identity.capability_id,
            graphir_node_id=node_id,
            decision=decision,
            target_file=best_fn.path,
            confidence=best_score,
            rationale=rationale,
        )

    @staticmethod
    def _default_path(identity: CanonicalIdentity) -> str:
        return f"src/components/{identity.component_name}.tsx"
