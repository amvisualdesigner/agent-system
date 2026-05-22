"""DELETE detection — state-diff based component removal.

Pure Core: zero IO, 100% deterministic.

DELETE is derived from state difference, NOT keyword parsing:

    DELETE = previous_state - current_intent_state

Where:
  previous_state    = fingerprint → MemoryRecord from persisted semantic memory
  current_intent_state = set of active canonical identity fingerprints

A component is deleted when:
  1. It exists in persisted memory (we previously created/updated it)
  2. The file still exists in the repository
  3. NO identity in the current intent graph produces its fingerprint

This is PURE state reconciliation. No NLP, no verb parsing, no force flags.

Phase 6a limitations:
  - rename/move semantics are NOT supported (modeled as delete + create)
  - import/whitespace cleanup after deletion is NOT performed
  - boundary accuracy is critical — unsafe deletes are skipped
"""

from __future__ import annotations

import logging

from app.graphir.constraint.models import MemoryRecord, DeletionRecord
from app.graphir.constraint.identity import CanonicalIdentity

logger = logging.getLogger(__name__)


def detect_deletions(
    memory: dict[str, MemoryRecord],
    active_identities: dict[str, CanonicalIdentity],
    file_nodes: dict[str, object],
) -> list[DeletionRecord]:
    """Detect components absent from current intent but present in memory/repo.

    Args:
        memory: dict[fingerprint, MemoryRecord] from RepositorySemanticMemory.load()
        active_identities: dict[graphir_node_id, CanonicalIdentity] from matcher
        file_nodes: dict[rel_path, FileNode] from RepositoryIndexer

    Returns:
        list of DeletionRecord — empty if no components to delete.

    Pure deterministic: no IO, no side effects, no heuristics.
    """
    active_fps = {id_.fingerprint() for id_ in active_identities.values()}
    deletions: list[DeletionRecord] = []

    for fp, rec in memory.items():
        if fp in active_fps:
            continue

        if rec.file_path not in file_nodes:
            continue

        deletions.append(DeletionRecord(
            fingerprint=fp,
            file_path=rec.file_path,
            component_name=rec.component_name,
        ))

    if deletions:
        logger.info(
            "DELETE detection: %d component(s) to remove",
            len(deletions),
        )
        for d in deletions:
            logger.debug("  DELETE: %s in %s", d.component_name, d.file_path)

    return deletions
