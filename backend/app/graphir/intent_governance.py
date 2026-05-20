"""Intent Governance — capability ontology health monitoring.

Provides analytical tools to detect ontology degradation:
  - Orphan capabilities: defined but unused by contracts/renderers/tests
  - Unresolved token frequency: top fragments the system can't understand
  - Capability co-occurrence: which intents appear together in practice
  - Embedding false positive tracking: inferred intents vs GraphIR materialization
  - Contract coverage gaps: frequent intents without compatible contracts

All trackers are accumulators: feed them DecompositionResult objects
over time and query reports for governance insights.

Usage:
    tracker = CoOccurrenceTracker()
    tracker.record(decompose_task("kpi and table"))
    tracker.record(decompose_task("dark timeseries chart"))
    report = tracker.report(top_n=5)
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.graphir.intent import CAPABILITY_REGISTRY, CapabilityDef
from app.graphir.intent_decomposition import DecompositionResult

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Orphan Capability Detector
# ═══════════════════════════════════════════════════════════════════


@dataclass
class OrphanReport:
    defined: list[str]
    referenced_in_contracts: list[str]
    referenced_in_patterns: list[str]
    referenced_in_graphir_maps: list[str]
    orphans: list[str]
    dead: list[str]


def _collect_referenced_ids() -> dict[str, set[str]]:
    """Collect capability IDs referenced across the system.

    Returns a dict of source → set of capability IDs.
    """
    sources: dict[str, set[str]] = {
        "defined": set(CAPABILITY_REGISTRY.keys()),
        "patterns": set(),
        "graphir_maps": set(),
        "contracts": set(),
    }

    # From _TASK_PATTERNS (intent_decomposition.py)
    from app.graphir.intent_decomposition import _TASK_PATTERNS
    for _keywords, capability, _ph, _fh, _st in _TASK_PATTERNS:
        sources["patterns"].add(capability)

    # From _CAPABILITY_TO_GRAPHIR_TYPE and _CAPABILITY_TO_EDGE_ROLE
    from app.graphir.intent import (
        _CAPABILITY_TO_GRAPHIR_TYPE,
        _CAPABILITY_TO_EDGE_ROLE,
    )
    sources["graphir_maps"].update(_CAPABILITY_TO_GRAPHIR_TYPE.keys())
    sources["graphir_maps"].update(_CAPABILITY_TO_EDGE_ROLE.keys())

    # From SKILL_CONTRACTS
    from app.contracts.skill_registry import SKILL_CONTRACTS
    for contract in SKILL_CONTRACTS.values():
        ast = contract.ast_template
        caps = (
            ast.get("capabilities", {}).values()
            if isinstance(ast, dict)
            else getattr(ast, "capabilities", {}).values()
        )
        sources["contracts"].update(caps)

    return sources


def detect_orphans() -> OrphanReport:
    """Find capabilities that are defined but unused.

    Orphan = defined in registry but NOT referenced by:
      - _TASK_PATTERNS
      - _CAPABILITY_TO_GRAPHIR_TYPE / _CAPABILITY_TO_EDGE_ROLE
      - Any contract's capability list

    Dead = defined + referenced somewhere but never produced in
    decomposition (requires runtime data — use UnresolvedTracker).
    """
    sources = _collect_referenced_ids()
    defined = sources["defined"]
    referenced = (
        sources["patterns"]
        | sources["graphir_maps"]
        | sources["contracts"]
    )
    orphans = sorted(defined - referenced)
    return OrphanReport(
        defined=sorted(defined),
        referenced_in_contracts=sorted(sources["contracts"]),
        referenced_in_patterns=sorted(sources["patterns"]),
        referenced_in_graphir_maps=sorted(sources["graphir_maps"]),
        orphans=orphans,
        dead=[],  # needs runtime data
    )


# ═══════════════════════════════════════════════════════════════════
# Unresolved Token Frequency Tracker
# ═══════════════════════════════════════════════════════════════════


@dataclass
class UnresolvedReport:
    total_tasks: int
    total_unresolved: int
    top_tokens: list[tuple[str, int]]
    top_tasks: list[tuple[str, int]]


class UnresolvedTracker:
    """Accumulates unresolved token frequencies across decomposition runs.

    Records which tokens the keyword+embedding system could not map
    to any capability. Useful for discovering vocabulary gaps.
    """

    def __init__(self) -> None:
        self._token_counts: Counter[str] = Counter()
        self._task_count: int = 0
        self._total_unresolved: int = 0

    def record(self, result: DecompositionResult) -> None:
        """Record unresolved tokens from a decomposition result."""
        self._task_count += 1
        for token in result.unresolved:
            self._token_counts[token] += 1
            self._total_unresolved += 1

    def report(self, top_n: int = 20) -> UnresolvedReport:
        return UnresolvedReport(
            total_tasks=self._task_count,
            total_unresolved=self._total_unresolved,
            top_tokens=self._token_counts.most_common(top_n),
            top_tasks=[],
        )

    @property
    def token_counts(self) -> Counter:
        return self._token_counts.copy()

    def merge(self, other: UnresolvedTracker) -> None:
        self._token_counts.update(other._token_counts)
        self._task_count += other._task_count
        self._total_unresolved += other._total_unresolved


# ═══════════════════════════════════════════════════════════════════
# Capability Co-Occurrence Tracker
# ═══════════════════════════════════════════════════════════════════


@dataclass
class CoOccurrenceReport:
    total_tasks: int
    pairs: list[tuple[tuple[str, str], int]]
    solo_counts: list[tuple[str, int]]


class CoOccurrenceTracker:
    """Tracks which capabilities appear together in the same task.

    Useful for discovering natural "macros" — capability combinations
    that frequently co-occur (e.g. kpi_row + timeseries for dashboards).
    """

    def __init__(self) -> None:
        self._pair_counts: Counter[tuple[str, str]] = Counter()
        self._solo_counts: Counter[str] = Counter()
        self._task_count: int = 0

    def record(self, result: DecompositionResult) -> None:
        """Record capability co-occurrence from a decomposition result."""
        caps = sorted({i.capability for i in result.intents})
        if not caps:
            return
        self._task_count += 1
        for cap in caps:
            self._solo_counts[cap] += 1
        for i in range(len(caps)):
            for j in range(i + 1, len(caps)):
                pair = (caps[i], caps[j])
                self._pair_counts[pair] += 1

    def report(self, top_n: int = 20) -> CoOccurrenceReport:
        return CoOccurrenceReport(
            total_tasks=self._task_count,
            pairs=self._pair_counts.most_common(top_n),
            solo_counts=self._solo_counts.most_common(top_n),
        )

    @property
    def pair_counts(self) -> Counter:
        return self._pair_counts.copy()

    def merge(self, other: CoOccurrenceTracker) -> None:
        self._pair_counts.update(other._pair_counts)
        self._solo_counts.update(other._solo_counts)
        self._task_count += other._task_count


# ═══════════════════════════════════════════════════════════════════
# Contract Coverage Gap Detector
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ContractCoverageReport:
    total_intents: int
    covered: int
    missing: list[tuple[str, int]]
    gap_capabilities: list[tuple[str, int]]


class ContractCoverageGapDetector:
    """Tracks intents that couldn't be matched to any contract.

    Accumulates over multiple decomposition runs to find capability
    gaps in the contract catalog.
    """

    def __init__(self) -> None:
        self._missing_counts: Counter[str] = Counter()
        self._total_intents: int = 0
        self._covered: int = 0

    def record(self, result: DecompositionResult) -> None:
        """Check each intent against the contract registry."""
        from app.graphir.intent_coverage import IntentCoverageValidator
        from app.contracts.skill_registry import list_contracts

        contracts = [c for c in list_contracts() if hasattr(c, "ast_template")]
        report = IntentCoverageValidator.check_coverage(result.intents, contracts)
        self._total_intents += report.total_intents
        self._covered += report.covered_intents
        for m in report.missing:
            self._missing_counts[m.capability] += 1

    def report(self, top_n: int = 10) -> ContractCoverageReport:
        return ContractCoverageReport(
            total_intents=self._total_intents,
            covered=self._covered,
            missing=self._missing_counts.most_common(top_n),
            gap_capabilities=self._missing_counts.most_common(top_n),
        )


# ═══════════════════════════════════════════════════════════════════
# Embedding False Positive Tracker
# ═══════════════════════════════════════════════════════════════════


@dataclass
class FPReport:
    total_inferred: int
    materialized: int
    false_positives: list[tuple[str, float]]


class EmbeddingFPTracker:
    """Tracks embedding-suggested intents that didn't materialize in GraphIR.

    Requires post-GraphIR feedback to detect false positives.
    """

    def __init__(self) -> None:
        self._inferred_caps: Counter[str] = Counter()
        self._materialized_caps: Counter[str] = Counter()

    def record_inferred(self, result: DecompositionResult) -> None:
        for cap in result.inferred:
            self._inferred_caps[cap] += 1

    def record_materialized(self, caps: list[str]) -> None:
        for cap in caps:
            self._materialized_caps[cap] += 1

    def report(self, top_n: int = 10) -> FPReport:
        fps = []
        for cap in self._inferred_caps:
            inferred_count = self._inferred_caps[cap]
            materialized_count = self._materialized_caps.get(cap, 0)
            if inferred_count > materialized_count:
                fp_rate = 1.0 - (materialized_count / inferred_count if inferred_count else 0)
                fps.append((cap, fp_rate))
        fps.sort(key=lambda x: x[1], reverse=True)
        return FPReport(
            total_inferred=sum(self._inferred_caps.values()),
            materialized=sum(self._materialized_caps.values()),
            false_positives=fps[:top_n],
        )
