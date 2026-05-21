"""CanonicalIdentity — deterministic fingerprint for intent→file mapping.

Each GraphIRNode produces one CanonicalIdentity.
Identity matching is checked BEFORE similarity scoring.

Two intents with the same fingerprint MUST map to the same file.
This is the "hard anchor" that prevents duplicate scaffolding.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CanonicalIdentity:
    """Deterministic fingerprint linking a semantic intent to a file.

    Composition: capability_id : sorted(domain) : params_hash

    Changing capabilities = new identity (different component type).
    Changing params = new identity (props changed, different file).
    Changing domain = new identity (semantic context shifted).
    """
    component_name: str
    capability_id: str
    domain: tuple[str, ...]
    params_hash: str
    graphir_node_id: str = ""

    def fingerprint(self) -> str:
        domain_part = ":".join(sorted(self.domain)) if self.domain else "generic"
        return f"{self.capability_id}:{domain_part}:{self.params_hash}"


def build_identities(graph: Any) -> dict[str, CanonicalIdentity]:
    """Build identities for all GraphIR nodes that have renderers.

    Args:
        graph: A GraphIR instance with .nodes dict.

    Returns:
        dict[graphir_node_id, CanonicalIdentity]
    """
    identities: dict[str, CanonicalIdentity] = {}

    for node in graph.nodes.values():
        params_hash = _compute_params_hash(node.data)
        domains = node.metadata.get("domain", [])
        if isinstance(domains, str):
            domains = [domains]
        capability = node.metadata.get("intent_capability") or node.metadata.get("capability", node.type)

        # Infer domain from capability ID when metadata has none
        if not domains and capability and "." in capability:
            domains = [capability.split(".")[0]]

        identities[node.id] = CanonicalIdentity(
            component_name=node.type,
            capability_id=capability,
            domain=tuple(domains) if domains else ("generic",),
            params_hash=params_hash,
            graphir_node_id=node.id,
        )

    return identities


def _compute_params_hash(params: dict[str, Any]) -> str:
    """Deterministic short hash of sorted params."""
    sorted_json = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(sorted_json.encode()).hexdigest()[:12]
