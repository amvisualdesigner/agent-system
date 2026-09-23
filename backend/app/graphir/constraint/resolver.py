"""IdentityResolver — deterministic decision kernel.

Pure Core: zero IO, 100% deterministic.

Takes ranked candidates from IntentFileMatcher and applies the
IDENTITY_SPEC rules to produce final FileOpDecisions.

Decision hierarchy (per IDENTITY_SPEC.md §4) — RESIDUAL, pending F3/F8:
  1. CanonicalIdentity.fingerprint() → check file_nodes[].canonical_ids
     → FOUND: UPDATE on pre-existing file (confidence 0.95)
  2. Component name → check file_nodes[].component_names (Phase 6a)
     → FOUND: UPDATE on containing file (confidence 0.85)
  3. Best candidate score → threshold comparison
     → score >= UPDATE_THRESHOLD: UPDATE
     → score >= EXTEND_THRESHOLD: EXTEND
     → score <  EXTEND_THRESHOLD: CREATE

F2 boundary (2026-09-22): Memory is history/evidence ONLY. It is no
longer a lifecycle input. RepositorySemanticMemory (load/merge/save)
persists historical identity→file facts but NEVER feeds this resolver.

F3 boundary (2026-09-22): these levels are EVIDENCE/PROPOSAL only. They
derive a candidate `decision` from repository evidence (index, scores,
overrides) and never observe or mutate the Confirmed Plan
(StructuralIR.operations).

RESIDUO F1 (KNOWN, NOT FIXED IN F3): apply_engine still projects
render_mode from this proposal, so it can still reach runtime lifecycle
through that single, documented point (apply_engine render_mode loop).
F3 isolates the resolver influence to that projection and proves it is
the ONLY path. F1 moves the projection's source to the Confirmed Plan
and removes this residual authority (G1 closes end-to-end).
"""

from __future__ import annotations

import logging

from app.engine.structural_index import StructuralIndex
from app.graphir.constraint.models import Decision, FileOpDecision
from app.graphir.constraint.identity import CanonicalIdentity

logger = logging.getLogger(__name__)


class IdentityResolver:
    """Pure Core: resolves candidates into decisions per IDENTITY_SPEC rules.

    F2: this resolver consumes repository evidence only. It no longer
    receives identity maps from semantic memory.

    Args:
        file_path_overrides: dict[component_type, real_repo_path] from
            _discover_repo_capability_files(). When set, overrides target_file
            so decisions land on real repo files (e.g., SalesOverviewPage.tsx
            instead of Page.tsx).
        structural_index: StructuralIndex for path derivation on CREATE.
    """

    UPDATE_THRESHOLD = 0.55
    EXTEND_THRESHOLD = 0.35

    def __init__(
        self,
        file_path_overrides: dict[str, str] | None = None,
        structural_index: StructuralIndex | None = None,
    ):
        self.file_path_overrides = file_path_overrides or {}
        self.structural_index = structural_index

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
            decisions[node_id] = self._apply_override(decision, identity, file_nodes)

        return decisions

    def _apply_override(
        self,
        decision: FileOpDecision,
        identity: CanonicalIdentity,
        file_nodes: dict[str, object],
    ) -> FileOpDecision:
        """Re-target a decision from repo evidence (physical hint).

        PROPOSAL-LEVEL (F3): this may change `decision` and `target_file`
        of the resolver's proposal, but it cannot observe or mutate the
        Confirmed Plan. Any runtime effect happens only through the
        render_mode residue in apply_engine (RESIDUO F1).
        """
        if not self.file_path_overrides:
            return decision
        override = self.file_path_overrides.get(identity.component_name)
        if override is None or override == decision.target_file:
            return decision
        new_decision = Decision.UPDATE if override in file_nodes else decision.decision
        logger.info(
            "Ownership override: %s target %s → %s (decision=%s)",
            identity.component_name, decision.target_file, override, new_decision,
        )
        return FileOpDecision(
            intent_id=decision.intent_id,
            graphir_node_id=decision.graphir_node_id,
            decision=new_decision,
            target_file=override,
            confidence=decision.confidence,
            rationale=f"{decision.rationale} | OWNERSHIP OVERRIDE → {override}",
        )

    def _decide(
        self,
        identity: CanonicalIdentity,
        node_id: str,
        candidates: dict[str, list[tuple[float, object]]],
        file_nodes: dict[str, object],
    ) -> FileOpDecision:
        fp = identity.fingerprint()
        node_candidates = candidates.get(node_id, [])

        # ── Level 1 (memory) was REMOVED (F2) ──
        # RepositorySemanticMemory no longer feeds lifecycle decisions.
        # Memory is history/evidence only. Pre-F2 this block returned
        # Decision.UPDATE from the persisted identity map.

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

    def _default_path(self, identity: CanonicalIdentity) -> str:
        if self.structural_index is not None:
            prefixes = self.structural_index.detect_component_prefixes()
            name = identity.component_name
            if name in prefixes:
                return prefixes[name]
            prefix = self.structural_index.most_common_component_prefix()
            if prefix is not None:
                return f"{prefix}/{name}.tsx"
        return f"src/components/{identity.component_name}.tsx"
