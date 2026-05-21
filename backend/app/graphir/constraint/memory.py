"""RepositorySemanticMemory — persists identity→file mappings.

State Layer: isolated IO to .opencode/semantic_memory.json.

Phase 2 scope:
  Load resolved_mapping → feed to IdentityResolver
  Persist new mappings after successful rendering

The memory is a dict[fingerprint, file_path] stored as JSON.
Fingerprints are CanonicalIdentity fingerprints (see IDENTITY_SPEC.md).

Rules:
  - Load on start: feed resolved_mapping to resolver (Level 1 priority)
  - Persist after each successful pipeline run
  - Merge preserves existing mappings; new identity→file pairs are additive
"""

from __future__ import annotations

import json
import logging
import os

from app.graphir.constraint.models import FileOpDecision
from app.graphir.constraint.identity import CanonicalIdentity

logger = logging.getLogger(__name__)


class RepositorySemanticMemory:
    """Persistent identity→file mapping stored as JSON.

    Args:
        memory_path: Full path to .opencode/semantic_memory.json.
            Provided by ExecutionContext.memory_path.

    Phase 2+: The loaded resolved_mapping is passed to IdentityResolver.
    After rendering, merge + save persists any new mappings.
    """

    def __init__(self, memory_path: str):
        self.memory_path = memory_path

    def load(self) -> dict[str, str]:
        """Load resolved_mapping from disk.

        Returns:
            dict[fingerprint, file_path] — empty dict if no file exists.

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
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load semantic memory: %s", e)
            return {}

    def save(self, mapping: dict[str, str]) -> None:
        """Persist resolved_mapping to disk atomically.

        Creates .opencode/ directory if it doesn't exist.
        """
        directory = os.path.dirname(self.memory_path)
        try:
            os.makedirs(directory, exist_ok=True)
            tmp_path = self.memory_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(mapping, f, indent=2, sort_keys=True)
            os.replace(tmp_path, self.memory_path)
        except OSError as e:
            logger.error("Failed to save semantic memory: %s", e)

    @staticmethod
    def merge(
        decisions: dict[str, FileOpDecision],
        identities: dict[str, CanonicalIdentity],
        existing: dict[str, str],
    ) -> dict[str, str]:
        """Merge new identity→file mappings into existing memory.

        Only stores mappings where:
        - The decision targets an existing file (UPDATE, EXTEND)
        - CREATE decisions are NOT stored (new file, no anchor yet)

        Args:
            decisions: dict[graphir_node_id, FileOpDecision] from resolver
            identities: dict[graphir_node_id, CanonicalIdentity] from matcher
            existing: current resolved_mapping (from load())

        Returns:
            Updated mapping with new entries added.
        """
        merged = dict(existing)

        for node_id, decision in decisions.items():
            identity = identities.get(node_id)
            if identity is None:
                continue

            # Only persist mappings for existing files (UPDATE/EXTEND).
            # CREATE means the file doesn't exist yet — no anchor.
            if decision.decision.value in ("modify", "extend"):
                fp = identity.fingerprint()
                merged[fp] = decision.target_file

        return merged
