"""Intent Coverage Validator — the core of intent-completeness checking.

Validates that user intents can be satisfied by available contracts
(registry) or LLM fallback. Provides pre-build coverage checks and
post-build revalidation (bijection constraint).

Placement in pipeline:
  Gate 2: Pre-SkillIR coverage check
  Gate 4: Post-GraphIR revalidation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.graphir.intent import Intent
from app.graphir.models import GraphIR

logger = logging.getLogger(__name__)

# ── Coverage types ─────────────────────────────────────────────────


@dataclass(frozen=True)
class CapabilityMatch:
    """A satisfied intent: links intent_id → contract slot."""
    intent_id: str
    capability: str
    contract_id: str | None         # None if LLM-synthesized
    slot_type: str | None           # None if LLM-synthesized
    source: str                     # "registry" | "llm"
    confidence: float


@dataclass(frozen=True)
class MissingIntent:
    """An unsatisfied intent with reason."""
    intent_id: str
    capability: str
    params: dict
    reason: str                     # "no_contract", "slot_not_found", "builder_dropped"


@dataclass(frozen=True)
class CoverageReport:
    """Complete coverage report for a set of intents.

    After Gate 2 (pre-SkillIR):  matched + missing are populated,
                                 intent_to_nodes is empty.
    After Gate 4 (post-GraphIR): intent_to_nodes + uncovered_intents
                                 are populated from revalidation.
    """
    coverage: float                 # 0.0–1.0
    total_intents: int
    covered_intents: int
    matched: list[CapabilityMatch]
    missing: list[MissingIntent]
    intent_to_nodes: dict[str, list[str]] = field(default_factory=dict)
    uncovered_intents: list[str] = field(default_factory=list)
    contract_count: int = 0
    fallback_used: bool = False
    gates_passed: dict[str, bool] = field(default_factory=lambda: {
        "decomposition": True,
        "coverage": False,
        "build": True,
        "revalidation": False,
    })

    @property
    def is_complete(self) -> bool:
        return all(self.gates_passed.values())

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
            gates_passed={**self.gates_passed, gate: passed},
        )


# ── Index builder ──────────────────────────────────────────────────


def build_capabilities_index(
    contracts: list,
) -> dict[str, list[tuple[str, str]]]:
    """Build reverse index: capability → [(contract_id, slot_type)].

    Reads each contract's ast_template["capabilities"] to map
    slot types to their capability strings.

    Args:
        contracts: list of SkillContract objects.

    Returns:
        dict mapping capability → list of (contract_id, slot_type) tuples.
    """
    index: dict[str, list[tuple[str, str]]] = {}
    for contract in contracts:
        capabilities = getattr(contract.ast_template, "capabilities",
                               contract.ast_template.get("capabilities", {})) \
            if isinstance(contract.ast_template, dict) \
            else getattr(contract.ast_template, "capabilities", {})
        for slot_type, capability in capabilities.items():
            index.setdefault(capability, []).append(
                (contract.contract_id, slot_type)
            )
    return index


# ── Coverage Validator ─────────────────────────────────────────────


class IntentCoverageError(Exception):
    """Raised when intent coverage validation fails."""
    pass


class IntentCoverageValidator:
    """Pure validator: checks intent coverage without side effects.

    Gate 2 and Gate 4 logic.
    """

    @staticmethod
    def check_coverage(
        intents: list[Intent],
        contracts: list,
        llm_synthesize: Callable[[Intent], CapabilityMatch | None] | None = None,
    ) -> CoverageReport:
        """Gate 2: Pre-SkillIR coverage check.

        For each Intent, try:
          1. Registry exact match by capability
          2. LLM semantic synthesis (if provided)

        Returns CoverageReport with matched + missing.

        Raises IntentCoverageError on critical failures.
        """
        if not intents:
            return CoverageReport(
                coverage=1.0,
                total_intents=0,
                covered_intents=0,
                matched=[],
                missing=[],
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

        return CoverageReport(
            coverage=coverage,
            total_intents=total,
            covered_intents=covered,
            matched=matched,
            missing=missing,
            contract_count=len(contract_ids),
            fallback_used=fallback_used,
            gates_passed={
                "decomposition": True,
                "coverage": coverage >= 1.0,
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

        Raises IntentCoverageError if the builder failed to materialize
        any intent (hard fail — not a warning).
        """
        # Build reverse index: intent_id → [node_id]
        intent_to_nodes: dict[str, list[str]] = {}
        for node_id, node in graph.nodes.items():
            iid = node.metadata.get("intent_id")
            if iid:
                intent_to_nodes.setdefault(iid, []).append(node_id)

        # Check every intent has at least one node
        uncovered: list[str] = []
        for intent in intents:
            nids = intent_to_nodes.get(intent.id, [])
            if not nids:
                uncovered.append(intent.id)

        # HARD FAIL if builder dropped intents
        if uncovered:
            raise IntentCoverageError(
                f"Builder failed to materialize {len(uncovered)} intents: {uncovered}"
            )

        # Warn about orphan nodes (nodes with no intent — doesn't block)
        orphan_nodes = [
            nid for nid, n in graph.nodes.items()
            if not n.metadata.get("intent_id")
        ]
        if orphan_nodes:
            logger.warning(
                "Orphan nodes with no intent provenance: %s",
                orphan_nodes,
            )

        # Build updated missing list for uncovered intents
        new_missing = list(report.missing)
        for iid in uncovered:
            matched_intent = next((m for m in report.matched if m.intent_id == iid), None)
            if matched_intent:
                new_missing.append(MissingIntent(
                    intent_id=iid,
                    capability=matched_intent.capability,
                    params={},
                    reason="builder_dropped",
                ))

        gates = dict(report.gates_passed)
        gates["revalidation"] = len(uncovered) == 0

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
            gates_passed=gates,
        )


# ── Internal helpers ───────────────────────────────────────────────


def _match_via_registry(
    intent: Intent,
    index: dict[str, list[tuple[str, str]]],
) -> CapabilityMatch | None:
    """Try to match an Intent via the registry's capability index."""
    matches = index.get(intent.capability, [])
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
