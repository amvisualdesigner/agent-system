"""SPLITAnalyzer — detects when a file should be split.

Pure Core: zero IO, 100% deterministic.

Runs after IdentityResolver produces decisions, before Renderer.
Analyzes whether any decision's target file has accumulated enough
components that a new component should be extracted to its own file.

Signals used:
  1. Component count threshold: file with >= N components + a decision
     targeting one of those components → extract to new file.
  2. CRL DUPLICATE_BINDING conflicts: multiple identities mapped to the
     same file → structural overload signal (Phase 4.1+).

Design rule: SPLITAnalyzer is ADD-only in Phase 4. It creates new files
for extracted components but does NOT modify the source file.
This avoids destructive refactoring and keeps the feedback loop safe.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from app.graphir.constraint.models import (
    Decision,
    FileOpDecision,
    SplitDirective,
    RefactoringPlan,
    ConflictType,
    ConflictRecord,
)
from app.graphir.constraint.identity import CanonicalIdentity

logger = logging.getLogger(__name__)


class SPLITAnalyzer:
    """Detects structural overload and produces RefactoringPlan.

    Args:
        threshold_component_count: Minimum components in a file before
            split is considered. Default 3 (heuristic — tunable).
    """

    def __init__(self, threshold_component_count: int = 3):
        self.threshold = threshold_component_count

    def analyze(
        self,
        decisions: dict[str, FileOpDecision],
        identities: dict[str, CanonicalIdentity],
        file_nodes: dict[str, object],
        crl_conflicts: list[ConflictRecord] | None = None,
    ) -> RefactoringPlan:
        """Produce split directives for overloaded files.

        Args:
            decisions: dict[graphir_node_id, FileOpDecision] from resolver
            identities: dict[graphir_node_id, CanonicalIdentity] from matcher
            file_nodes: dict[rel_path, FileNode] from indexer
            crl_conflicts: list[ConflictRecord] from CRL (optional, Phase 4.1+)

        Returns:
            RefactoringPlan with splits for overloaded files.
        """
        splits: list[SplitDirective] = []
        seen: set[tuple[str, str]] = set()  # (source_file, component) dedup

        # ── Signal 1: Component count threshold ──
        for node_id, decision in decisions.items():
            if decision.decision not in (Decision.UPDATE, Decision.EXTEND):
                continue

            fn = file_nodes.get(decision.target_file)
            if fn is None:
                continue

            comp_names = getattr(fn, "component_names", [])
            if len(comp_names) < self.threshold:
                continue

            identity = identities.get(node_id)
            if identity is None:
                continue

            if identity.component_name not in comp_names:
                continue

            key = (decision.target_file, identity.component_name)
            if key in seen:
                continue
            seen.add(key)

            new_file = f"src/components/{identity.component_name}.tsx"
            splits.append(SplitDirective(
                source_file=decision.target_file,
                new_file=new_file,
                components_to_extract=[identity.component_name],
            ))
            logger.info(
                "SPLIT: %s → %s (extracted from %s, %d components)",
                identity.component_name, new_file,
                decision.target_file, len(comp_names),
            )

        # ── Signal 2: CRL DUPLICATE_BINDING (Phase 4.1+) ──
        # Reserved for future use. When conflicts indicate structural
        # overload, generate splits for non-primary components.
        #
        # For now, DUPLICATE_BINDING alone does NOT trigger a split
        # (requires component count confirmation).

        return RefactoringPlan(splits=splits, blocks=[])
