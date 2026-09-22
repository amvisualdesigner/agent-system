"""RepositorySemanticMemory — historical identity→file evidence store.

State Layer: isolated IO to .opencode/semantic_memory.json.

F2 boundary (2026-09-22):
  Memory is HISTORY/EVIDENCE. It is never a lifecycle authority.
  load()/merge()/save() persist historical identity→file facts
  (including CREATE history) and feed the audit trail, but nothing
  downstream may turn a saved fact into a Decision/lifecycle change.

Phase 6a:
  Memory stores MemoryRecord (fingerprint, file_path, component_name)
  instead of bare file_path strings. Backward compatible: old-format
  dicts are migrated on load.

Rules:
  - Load on start → evidence for audit + existing base for merge
  - Persist after each successful pipeline run (historical record)
  - Merge preserves existing mappings; new identity→file pairs are additive
  - DELETE fingerprints are removed from memory via deleted_fingerprints
"""

from __future__ import annotations

import json
import logging
import os

from app.graphir.constraint.models import FileOpDecision, MemoryRecord
from app.graphir.constraint.identity import CanonicalIdentity

logger = logging.getLogger(__name__)


def _memory_record_from_raw(key: str, value: object) -> MemoryRecord | None:
    """Parse a single memory entry, supporting both old and new formats.

    Old format (pre-Phase 6a):
        "fingerprint": "src/components/KpiRow.tsx"

    New format (Phase 6a+):
        "fingerprint": {"fingerprint": "...", "file_path": "...", "component_name": "..."}

    Returns None if the entry is corrupt and should be skipped.
    """
    if isinstance(value, str):
        # Old format — wrap with unknown component_name
        logger.debug("Migrating old-format memory entry: %s → %s", key, value)
        return MemoryRecord(fingerprint=key, file_path=value, component_name="")

    if isinstance(value, dict):
        fp = value.get("fingerprint", key)
        file_path = value.get("file_path", "")
        component_name = value.get("component_name", "")
        if not file_path:
            logger.warning("Memory entry %s has no file_path — skipping", key)
            return None
        return MemoryRecord(fingerprint=fp, file_path=file_path, component_name=component_name)

    logger.warning("Unexpected memory entry type for %s: %s — skipping", key, type(value).__name__)
    return None


class RepositorySemanticMemory:
    """Persistent identity→file mapping stored as JSON.

    Args:
        memory_path: Full path to .opencode/semantic_memory.json.
            Provided by ExecutionContext.memory_path.

    F2: loaded facts are EVIDENCE. They are never passed to IdentityResolver
    as a lifecycle input. After rendering, merge + save persists the
    historical record.

    Phase 6a+: Memory stores dict[fingerprint, MemoryRecord].
    """

    def __init__(self, memory_path: str):
        self.memory_path = memory_path

    def load(self) -> dict[str, MemoryRecord]:
        """Load resolved_mapping from disk.

        Supports both old-format (str values) and new-format (dict values).
        Old entries are migrated with an empty component_name.

        Returns:
            dict[fingerprint, MemoryRecord] — empty if no file exists or corrupt.

        Pure read: no side effects. Missing file = empty memory.
        """
        if not os.path.exists(self.memory_path):
            return {}

        try:
            with open(self.memory_path, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("semantic_memory is not a dict, resetting")
                return {}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load semantic memory: %s", e)
            return {}

        result: dict[str, MemoryRecord] = {}
        for key, value in data.items():
            rec = _memory_record_from_raw(key, value)
            if rec is not None:
                result[key] = rec

        return result

    def save(self, mapping: dict[str, MemoryRecord]) -> None:
        """Persist resolved_mapping to disk atomically.

        Creates .opencode/ directory if it doesn't exist.
        Writes in Phase 6a format (dict values with fingerprint, file_path, component_name).
        """
        directory = os.path.dirname(self.memory_path)
        try:
            os.makedirs(directory, exist_ok=True)
            serializable = {
                fp: {
                    "fingerprint": rec.fingerprint,
                    "file_path": rec.file_path,
                    "component_name": rec.component_name,
                }
                for fp, rec in mapping.items()
            }
            tmp_path = self.memory_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(serializable, f, indent=2, sort_keys=True)
            os.replace(tmp_path, self.memory_path)
        except OSError as e:
            logger.error("Failed to save semantic memory: %s", e)

    @staticmethod
    def merge(
        decisions: dict[str, FileOpDecision],
        identities: dict[str, CanonicalIdentity],
        existing: dict[str, MemoryRecord],
        deleted_fingerprints: set[str] | None = None,
    ) -> dict[str, MemoryRecord]:
        """Merge new identity→file mappings into existing memory.

        Stores historical identity→file facts additively (including CREATE,
        extend, modify). These facts are evidence only (F2): they are never
        consumed as a lifecycle input downstream.
        DELETE fingerprints are removed from the result.

        Args:
            decisions: dict[graphir_node_id, FileOpDecision] from resolver
            identities: dict[graphir_node_id, CanonicalIdentity] from matcher
            existing: current resolved_mapping (from load())
            deleted_fingerprints: fingerprints to remove from memory (Phase 6a)

        Returns:
            Updated mapping with new entries added and deleted entries removed.
        """
        merged = dict(existing)

        for node_id, decision in decisions.items():
            identity = identities.get(node_id)
            if identity is None:
                continue

            if decision.decision.value in ("modify", "extend", "create"):
                fp = identity.fingerprint()
                merged[fp] = MemoryRecord(
                    fingerprint=fp,
                    file_path=decision.target_file,
                    component_name=identity.component_name,
                )

        if deleted_fingerprints:
            for fp in deleted_fingerprints:
                merged.pop(fp, None)

        return merged
