"""ConflictResolutionLayer — reconciles memory against repository state.

Pure Core: zero IO, 100% deterministic.

Runs on the historical memory read. F2: the reconciled output is
EVIDENCE for the audit trail — it is no longer passed to IdentityResolver
as a lifecycle input (memory is never a lifecycle authority).

Design rule: CRL is an orchestrator, NOT a second matcher.
  - STALE_MAPPING → just check file existence (no scoring)
  - MISSING_TARGET → conservative: invalidate if file is empty
  - DUPLICATE_BINDING → benign, keep both (resolver is deterministic)
  - No REBIND scoring: after INVALIDATE the residual resolver scoring
    fallback handles the lifecycle, and F1/F3 will remove that path.

This keeps CRL lean and avoids duplicating matcher._compute_score().
"""

from __future__ import annotations

import logging

from app.graphir.constraint.models import (
    ConflictRecord,
    ConflictType,
    ResolutionStrategy,
)

logger = logging.getLogger(__name__)


class ConflictResolutionLayer:
    """Reconciles historical memory mappings against current file_nodes.

    Usage:
        crl = ConflictResolutionLayer()
        cleaned, conflicts = crl.resolve(memory_mapping, file_nodes)
        # F2: cleaned is evidence only; NOT passed to IdentityResolver.
    """

    @staticmethod
    def resolve(
        memory_mapping: dict[str, str],
        file_nodes: dict[str, object],
    ) -> tuple[dict[str, str], list[ConflictRecord]]:
        """Reconcile memory mappings against current file state.

        Args:
            memory_mapping: dict[fingerprint, file_path] from memory.load()
            file_nodes: dict[rel_path, FileNode] from RepositoryIndexer

        Returns:
            (cleaned_mapping, conflicts)
            cleaned_mapping: surviving mappings after reconciliation
            conflicts: list of ConflictRecord for observability
        """
        cleaned: dict[str, str] = {}
        conflicts: list[ConflictRecord] = []

        # Track paths seen to detect DUPLICATE_BINDING
        path_to_fingerprints: dict[str, list[str]] = {}

        for fp, path in memory_mapping.items():
            # ── STALE_MAPPING: file no longer exists ──
            if path not in file_nodes:
                conflicts.append(ConflictRecord(
                    identity_fingerprint=fp,
                    conflict_type=ConflictType.STALE_MAPPING,
                    expected_file=path,
                    severity=0.8,
                    resolution=ResolutionStrategy.INVALIDATE,
                    metadata={"reason": "file_not_found"},
                ))
                logger.info("CRL: stale mapping %s → %s (removed)", fp, path)
                continue

            # ── MISSING_TARGET: file exists but is empty ──
            fn = file_nodes[path]
            has_exports = bool(getattr(fn, "exports", []))
            has_components = bool(getattr(fn, "component_names", []))

            if not has_exports and not has_components:
                conflicts.append(ConflictRecord(
                    identity_fingerprint=fp,
                    conflict_type=ConflictType.MISSING_TARGET,
                    expected_file=path,
                    actual_file=path,
                    severity=0.6,
                    resolution=ResolutionStrategy.INVALIDATE,
                    metadata={"reason": "file_has_no_exports"},
                ))
                logger.info("CRL: missing target %s → %s (empty, removed)", fp, path)
                continue

            # ── Mapping survives ──
            cleaned[fp] = path
            path_to_fingerprints.setdefault(path, []).append(fp)

        # ── DUPLICATE_BINDING: detect after building cleaned ──
        for path, fps in path_to_fingerprints.items():
            if len(fps) > 1:
                for fp in fps:
                    conflicts.append(ConflictRecord(
                        identity_fingerprint=fp,
                        conflict_type=ConflictType.DUPLICATE_BINDING,
                        expected_file=path,
                        actual_file=path,
                        severity=0.3,
                        resolution=ResolutionStrategy.KEEP,
                        metadata={"bound_fingerprints": fps},
                    ))
                logger.info(
                    "CRL: duplicate binding %s → %s (%d fingerprints, kept)",
                    path, fps, len(fps),
                )

        return cleaned, conflicts
