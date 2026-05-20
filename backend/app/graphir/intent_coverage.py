"""Intent Coverage Validator — the core of intent-completeness checking.

Validates that user intents can be satisfied by available contracts
(registry) or LLM fallback. Provides pre-build coverage checks and
post-build revalidation (bijection constraint).

Placement in pipeline:
  Gate 2: Pre-SkillIR coverage check
  Gate 4: Post-GraphIR revalidation

Phase 1 additions:
  - decomposition_confidence: how well the task was understood
  - semantic_entropy: ambiguity of the decomposition
  - Soft intents (layout.*, style.*) never block the pipeline,
    only degrade fidelity/confidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.graphir.intent import Intent, is_capability_soft, compute_semantic_entropy
from app.graphir.models import GraphIR

logger = logging.getLogger(__name__)


# ── Coverage types ─────────────────────────────────────────────────


@dataclass(frozen=True)
class CapabilityMatch:
    intent_id: str
    capability: str
    contract_id: str | None
    slot_type: str | None
    source: str
    confidence: float


@dataclass(frozen=True)
class MissingIntent:
    intent_id: str
    capability: str
    params: dict
    reason: str


@dataclass(frozen=True)
class CoverageReport:
    coverage: float
    total_intents: int
    covered_intents: int
    matched: list[CapabilityMatch]
    missing: list[MissingIntent]
    intent_to_nodes: dict[str, list[str]] = field(default_factory=dict)
    uncovered_intents: list[str] = field(default_factory=list)
    contract_count: int = 0
    fallback_used: bool = False
    decomposition_confidence: float = 1.0
    semantic_entropy: float = 0.0
    detected_intents: list[str] = field(default_factory=list)
    inferred_intents: list[str] = field(default_factory=list)
    unresolved_fragments: list[str] = field(default_factory=list)
    gates_passed: dict[str, bool] = field(default_factory=lambda: {
        "decomposition": True,
        "coverage": False,
        "build": True,
        "revalidation": False,
    })

    @property
    def is_complete(self) -> bool:
        return all(self.gates_passed.values())

    @property
    def hard_coverage(self) -> float:
        """Coverage excluding soft + metadata-only intents.

        Soft intents (layout.*, style.*) and metadata-only capabilities
        (domain.*, layout.grid, layout.container) never block the pipeline.
        """
        from app.graphir.intent import is_capability_metadata
        non_blocking = lambda c: is_capability_soft(c) or is_capability_metadata(c)

        hard_total = self.total_intents - len(
            [m for m in self.matched if non_blocking(m.capability)]
        ) - len(
            [m for m in self.missing if non_blocking(m.capability)]
        )
        if hard_total <= 0:
            return 1.0
        hard_covered = self.covered_intents - len(
            [m for m in self.matched if non_blocking(m.capability)]
        )
        return hard_covered / hard_total

    def with_gate(self, gate: str, passed: bool) -> "CoverageReport":
        return CoverageReport(
            coverage=self.coverage,
            total_intents=self.total_intents,
            covered_intents=self.covered_intents,
            matched=self.matched,
            missing=self.missing,
            intent_to_nodes=self.intent_to_nodes,
            uncovered_intents=self.uncovered_intents,
            contract_count=self.contract_count,
            fallback_used=self.fallback_used,
            decomposition_confidence=self.decomposition_confidence,
            semantic_entropy=self.semantic_entropy,
            detected_intents=self.detected_intents,
            inferred_intents=self.inferred_intents,
            unresolved_fragments=self.unresolved_fragments,
            gates_passed={**self.gates_passed, gate: passed},
        )


# ── Index builder ──────────────────────────────────────────────────


def build_capabilities_index(
    contracts: list,
) -> dict[str, list[tuple[str, str]]]:
    """Build reverse index: capability → [(contract_id, slot_type)].

    Includes alias entries so deprecated capability strings
    (e.g. "display.kpi_row") resolve to contracts that declare
    canonical IDs (e.g. "presentation.kpi_row").
    """
    from app.graphir.intent import _CAPABILITY_ALIASES
    rev_aliases = {v: k for k, v in _CAPABILITY_ALIASES.items()}

    index: dict[str, list[tuple[str, str]]] = {}
    for contract in contracts:
        capabilities = getattr(contract.ast_template, "capabilities",
                               contract.ast_template.get("capabilities", {})) \
            if isinstance(contract.ast_template, dict) \
            else getattr(contract.ast_template, "capabilities", {})
        for slot_type, capability in capabilities.items():
            # Index by canonical ID
            index.setdefault(capability, []).append(
                (contract.contract_id, slot_type)
            )
            # Also index by any alias that points to this canonical ID
            alias = rev_aliases.get(capability)
            if alias and alias != capability:
                index.setdefault(alias, []).append(
                    (contract.contract_id, slot_type)
                )
    return index


# ── Coverage Validator ─────────────────────────────────────────────


class IntentCoverageError(Exception):
    pass


class IntentCoverageValidator:

    @staticmethod
    def check_coverage(
        intents: list[Intent],
        contracts: list,
        llm_synthesize: Callable[[Intent], CapabilityMatch | None] | None = None,
        task: str | None = None,
        decomposition_confidence: float | None = None,
        detected_intents: list[str] | None = None,
        inferred_intents: list[str] | None = None,
        unresolved_fragments: list[str] | None = None,
    ) -> CoverageReport:
        """Gate 2: Pre-SkillIR coverage check.

        Soft intents (layout.*, style.*) are included in the report
        but do NOT cause gate failure. They only degrade fidelity.

        Args:
            intents: Decomposed intents.
            contracts: Available contracts.
            llm_synthesize: Optional LLM fallback for unmatched intents.
            task: Original task string (for computing semantic_entropy).
            detected_intents: Capabilities detected with high confidence.
            inferred_intents: Capabilities inferred with lower confidence.
            unresolved_fragments: Task fragments that no pattern matched.

        Returns:
            CoverageReport with decomposition confidence and semantic entropy.
        """
        if not intents:
            return CoverageReport(
                coverage=1.0,
                total_intents=0,
                covered_intents=0,
                matched=[],
                missing=[],
                detected_intents=detected_intents or [],
                inferred_intents=inferred_intents or [],
                unresolved_fragments=unresolved_fragments or [],
            )

        index = build_capabilities_index(contracts)
        matched: list[CapabilityMatch] = []
        missing: list[MissingIntent] = []
        fallback_used = False
        contract_ids: set[str] = set()

        for intent in intents:
            match = _match_via_registry(intent, index)
            if match is not None:
                matched.append(match)
                if match.contract_id:
                    contract_ids.add(match.contract_id)
                continue

            if llm_synthesize is not None:
                llm_match = llm_synthesize(intent)
                if llm_match is not None:
                    matched.append(llm_match)
                    fallback_used = True
                    continue

            missing.append(MissingIntent(
                intent_id=intent.id,
                capability=intent.capability,
                params=intent.params,
                reason="no_contract",
            ))

        total = len(intents)
        covered = len(matched)
        coverage = covered / total if total > 0 else 1.0

        # Compute decomposition_confidence — use provided value from planner
        # to avoid recomputation divergence between phases, fall back to recalc
        if decomposition_confidence is not None:
            dec_confidence = decomposition_confidence
        else:
            from app.graphir.intent_decomposition import compute_decomposition_confidence
            dec_confidence = compute_decomposition_confidence(task or "", intents)

        # Compute semantic entropy (always use Intent objects, not CapabilityMatch)
        entropy = compute_semantic_entropy(task or "", intents)

        # Gate passes if hard (non-soft, non-metadata) coverage >= 1.0
        from app.graphir.intent import is_capability_metadata
        non_blocking = lambda c: is_capability_soft(c) or is_capability_metadata(c)
        hard_covered = len([m for m in matched if not non_blocking(m.capability)])
        hard_total = len([i for i in intents if not non_blocking(i.capability)])
        hard_cov = hard_covered / hard_total if hard_total > 0 else 1.0

        return CoverageReport(
            coverage=coverage,
            total_intents=total,
            covered_intents=covered,
            matched=matched,
            missing=missing,
            contract_count=len(contract_ids),
            fallback_used=fallback_used,
            decomposition_confidence=dec_confidence,
            semantic_entropy=entropy,
            detected_intents=detected_intents or [],
            inferred_intents=inferred_intents or [],
            unresolved_fragments=unresolved_fragments or [],
            gates_passed={
                "decomposition": True,
                "coverage": hard_cov >= 1.0,
                "build": True,
                "revalidation": False,
            },
        )

    @staticmethod
    def revalidate(
        graph: GraphIR,
        report: CoverageReport,
        intents: list[Intent],
    ) -> CoverageReport:
        """Gate 4: Post-GraphIR coverage revalidation.

        Checks the bijection constraint:
          - Every Intent.id → at least one GraphIRNode.id
          - Every GraphIRNode.metadata.intent_id → valid Intent

        Soft intents (layout.*, style.*) that fail revalidation do NOT
        raise IntentCoverageError — they are tracked as uncovered
        but treated as degradations, not hard failures.
        """
        intent_to_nodes: dict[str, list[str]] = {}
        for node_id, node in graph.nodes.items():
            iid = node.metadata.get("intent_id")
            if iid:
                intent_to_nodes.setdefault(iid, []).append(node_id)

        uncovered: list[str] = []
        for intent in intents:
            nids = intent_to_nodes.get(intent.id, [])
            if not nids:
                uncovered.append(intent.id)

        # Separate hard, metadata-only, and soft uncovered
        from app.graphir.intent import is_capability_metadata
        hard_uncovered = [
            uid for uid in uncovered
            if not is_capability_soft(next(
                (i.capability for i in intents if i.id == uid), ""
            )) and not is_capability_metadata(next(
                (i.capability for i in intents if i.id == uid), ""
            ))
        ]
        metadata_uncovered = [
            uid for uid in uncovered
            if is_capability_metadata(next(
                (i.capability for i in intents if i.id == uid), ""
            ))
        ]
        soft_uncovered = [
            uid for uid in uncovered
            if is_capability_soft(next(
                (i.capability for i in intents if i.id == uid), ""
            ))
        ]

        # HARD FAIL only for non-soft, non-metadata uncovered intents
        if hard_uncovered:
            raise IntentCoverageError(
                f"Builder failed to materialize {len(hard_uncovered)} hard intents: {hard_uncovered}"
            )

        # Warn about soft/metadata uncovered (degradation, not failure)
        if soft_uncovered:
            logger.warning(
                "Soft intents not materialized (degradation): %s",
                soft_uncovered,
            )
        if metadata_uncovered:
            logger.warning(
                "Metadata-only intents not materialized (expected): %s",
                metadata_uncovered,
            )

        orphan_nodes = [
            nid for nid, n in graph.nodes.items()
            if not n.metadata.get("intent_id")
        ]
        if orphan_nodes:
            logger.warning(
                "Orphan nodes with no intent provenance: %s",
                orphan_nodes,
            )

        metadata_uncovered_set = set(metadata_uncovered)
        new_missing = list(report.missing)
        for iid in uncovered:
            if iid in metadata_uncovered_set:
                continue
            matched_intent = next((m for m in report.matched if m.intent_id == iid), None)
            if matched_intent:
                new_missing.append(MissingIntent(
                    intent_id=iid,
                    capability=matched_intent.capability,
                    params={},
                    reason="builder_dropped",
                ))

        gates = dict(report.gates_passed)
        gates["revalidation"] = len(hard_uncovered) == 0

        return CoverageReport(
            coverage=report.coverage,
            total_intents=report.total_intents,
            covered_intents=report.covered_intents - len(uncovered),
            matched=report.matched,
            missing=new_missing,
            intent_to_nodes=intent_to_nodes,
            uncovered_intents=uncovered,
            contract_count=report.contract_count,
            fallback_used=report.fallback_used,
            decomposition_confidence=report.decomposition_confidence,
            semantic_entropy=report.semantic_entropy,
            detected_intents=report.detected_intents,
            inferred_intents=report.inferred_intents,
            unresolved_fragments=report.unresolved_fragments,
            gates_passed=gates,
        )


# ── Internal helpers ───────────────────────────────────────────────


def _normalize_capability(cap: str) -> str:
    """Resolve deprecated capability aliases to canonical IDs."""
    from app.graphir.intent import _CAPABILITY_ALIASES
    return _CAPABILITY_ALIASES.get(cap, cap)


def _match_via_registry(
    intent: Intent,
    index: dict[str, list[tuple[str, str]]],
) -> CapabilityMatch | None:
    # Try exact match first
    matches = index.get(intent.capability, [])
    # Try canonical (aliased) match
    if not matches:
        canonical = _normalize_capability(intent.capability)
        if canonical != intent.capability:
            matches = index.get(canonical, [])
    if not matches:
        return None
    contract_id, slot_type = matches[0]
    return CapabilityMatch(
        intent_id=intent.id,
        capability=intent.capability,
        contract_id=contract_id,
        slot_type=slot_type,
        source="registry",
        confidence=1.0,
    )
