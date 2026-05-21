"""IntentFileMatcher — pure scorer: produces ranked candidate lists.

Pure Core: zero IO, 100% deterministic.

This module has NO decision logic. It only scores identity→file
pairs and returns ranked candidates. Decision logic lives in
IdentityResolver (resolver.py).

Usage:
    matcher = IntentFileMatcher()
    identities, candidates = matcher.match(graph, file_nodes)
    # identities: dict[graphir_node_id, CanonicalIdentity]
    # candidates: dict[graphir_node_id, list[tuple[float, FileNode]]]
"""

from __future__ import annotations

import logging
import os
import re

from app.graphir.constraint.identity import CanonicalIdentity, build_identities

logger = logging.getLogger(__name__)


class IntentFileMatcher:
    """Pure Core: scores identities against file nodes.

    Produces ranked candidate lists. Makes NO decisions —
    that is IdentityResolver's responsibility.
    """

    SCORE_WEIGHTS = {
        "semantic_similarity": 0.30,
        "symbol_overlap": 0.30,
        "domain_overlap": 0.00,
        "path_proximity": 0.40,
    }

    def match(
        self,
        graph: object,
        file_nodes: dict[str, object],
    ) -> tuple[dict[str, CanonicalIdentity], dict[str, list[tuple[float, object]]]]:
        """Score every identity against every file node.

        Args:
            graph: GraphIR instance with .nodes
            file_nodes: dict[rel_path, FileNode]

        Returns:
            (identities, candidates)
            identities: dict[graphir_node_id, CanonicalIdentity]
            candidates: dict[graphir_node_id, list[(score, FileNode)]]
                sorted descending by score.
        """
        identities = build_identities(graph)
        candidates: dict[str, list[tuple[float, object]]] = {}

        for node_id, identity in identities.items():
            scored: list[tuple[float, object]] = []
            for fn in file_nodes.values():
                score = self._compute_score(identity, fn)
                scored.append((score, fn))
            scored.sort(key=lambda x: -x[0])
            candidates[node_id] = scored

        return identities, candidates

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Tokenize text splitting on spaces, ., _, and CamelCase boundaries."""
        text = re.sub(r'([a-z])([A-Z])', r'\1_\2', text)
        text = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', text)
        text = re.sub(r'[\s._]+', ' ', text)
        tokens = set()
        for part in text.strip().lower().split():
            if part:
                tokens.add(part)
        return tokens

    def _compute_score(self, identity: CanonicalIdentity, file_node) -> float:
        """Deterministic weighted score: how well does identity match file_node?"""
        cap = identity.capability_id
        file_stem = os.path.splitext(os.path.basename(file_node.path))[0]
        exports_text = " ".join(getattr(file_node, "exports", []))
        names_text = " ".join(getattr(file_node, "component_names", []))

        sem_text = f"{exports_text} {file_stem}".strip() or names_text
        sem = self._dice(cap.lower(), sem_text.lower())

        cap_tokens = self._tokenize(cap)
        file_tokens = self._tokenize(sem_text)
        sym = self._jaccard(cap_tokens, file_tokens)

        dom = self._jaccard(
            set(identity.domain),
            set(getattr(file_node, "domains", [])),
        )

        path = 1.0 if identity.component_name.lower() == file_stem.lower() else 0.0

        return (
            self.SCORE_WEIGHTS["semantic_similarity"] * sem
            + self.SCORE_WEIGHTS["symbol_overlap"] * sym
            + self.SCORE_WEIGHTS["domain_overlap"] * dom
            + self.SCORE_WEIGHTS["path_proximity"] * path
        )

    @staticmethod
    def _dice(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        a_bigrams = set(a[i:i+2] for i in range(len(a)-1))
        b_bigrams = set(b[i:i+2] for i in range(len(b)-1))
        if not a_bigrams and not b_bigrams:
            return 1.0
        intersection = a_bigrams & b_bigrams
        return 2.0 * len(intersection) / (len(a_bigrams) + len(b_bigrams))

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        if not a and not b:
            return 0.0
        union = a | b
        if not union:
            return 0.0
        return len(a & b) / len(union)
