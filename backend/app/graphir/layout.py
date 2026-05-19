"""LayoutDerivationEngine — the ONLY layout authority in the system.

Translates GraphIR (nodes + edges + metadata) into abstract
LayoutConstraints. Each backend interprets these constraints
into framework-specific styling.

CRITICAL: LayoutDerivationEngine must NEVER contain:
  - if/switch by node.type
  - if/switch by framework name
  - Hardcoded layout exceptions for specific node IDs
  - Knowledge of renderer backends

Any such conditional is a regression to slot logic or template authority.
"""

from __future__ import annotations

from typing import Any

from app.graphir.models import (
    EdgeRole,
    LayoutConstraint,
    GraphIR,
    GraphIREdge,
    GraphIRLayout,
    GraphIRNode,
)


class LayoutDerivationEngine:
    """PURE function: GraphIR → GraphIRLayout.

    LayoutDerivationEngine is the ONLY component that translates
    semantic composition into spatial constraints.

    Properties:
      - Stateless (pure function of GraphIR + optional preferences)
      - Deterministic (same GraphIR → same LayoutConstraints)
      - Testable (unit tests for each derivation rule)
      - Renderer-agnostic (no framework knowledge)

    Derivation rules:
      - PRIMARY children    → FULL_WIDTH (single) or ROW (multiple)
      - SUPPORTING children → COLUMN or HALF_WIDTH
      - CONTAINS children   → STACK (default flow)

    metadata.priority can override default derivation:
      - priority="high"   → FULL_WIDTH regardless of role
      - priority="low"    → THIRD_WIDTH or HIDDEN
      - priority="hidden" → HIDDEN
    """

    # Derivation table: EdgeRole → (single_child, multiple_children)
    _ROLE_TO_CONSTRAINT: dict[EdgeRole, tuple[LayoutConstraint, LayoutConstraint]] = {
        EdgeRole.PRIMARY: (LayoutConstraint.FULL_WIDTH, LayoutConstraint.ROW),
        EdgeRole.SUPPORTING: (LayoutConstraint.HALF_WIDTH, LayoutConstraint.COLUMN),
        EdgeRole.CONTAINS: (LayoutConstraint.STACK, LayoutConstraint.STACK),
    }

    # Priority overrides (metadata.priority → constraint)
    _PRIORITY_OVERRIDES: dict[str, LayoutConstraint] = {
        "high": LayoutConstraint.FULL_WIDTH,
        "low": LayoutConstraint.THIRD_WIDTH,
        "hidden": LayoutConstraint.HIDDEN,
    }

    @classmethod
    def derive(cls, graph: GraphIR, preferences: dict | None = None) -> GraphIRLayout:
        """Derive spatial constraints from GraphIR.

        Args:
            graph: A frozen, validated GraphIR.
            preferences: Optional dict with layout hints
                (e.g. {"preferred_columns": 2}). Not required for basic operation.

        Returns:
            GraphIRLayout with constraints for every node.

        Raises:
            ValueError if graph is empty or malformed.
        """
        if not graph.nodes:
            raise ValueError("LayoutDerivationEngine: cannot derive layout from empty graph")

        root = graph.layout.root if graph.layout and graph.layout.root else _find_root(graph)
        constraints: dict[str, list[LayoutConstraint]] = {}
        children_by_parent: dict[str, list[GraphIREdge]] = {}

        for edge in graph.edges:
            children_by_parent.setdefault(edge.source, []).append(edge)

        for node_id, node in graph.nodes.items():
            node_constraints: list[LayoutConstraint] = []

            # Priority override (metadata-based, not type-based)
            priority = node.metadata.get("priority", "")
            if priority in cls._PRIORITY_OVERRIDES:
                node_constraints.append(cls._PRIORITY_OVERRIDES[priority])
                constraints[node_id] = node_constraints
                continue

            # Incoming edges determine this node's constraint within its parent
            incoming = [e for e in graph.edges if e.target == node_id]
            if incoming:
                role_counts: dict[EdgeRole, int] = {}
                for edge in incoming:
                    role_counts[edge.role] = role_counts.get(edge.role, 0) + 1

                for role, count in role_counts.items():
                    single_or_multi = cls._ROLE_TO_CONSTRAINT.get(role)
                    if single_or_multi:
                        constraint = single_or_multi[1] if count > 1 else single_or_multi[0]
                        if constraint not in node_constraints:
                            node_constraints.append(constraint)
            else:
                # Root node (no incoming) gets FULL_WIDTH by default
                node_constraints.append(LayoutConstraint.FULL_WIDTH)

            constraints[node_id] = node_constraints

        return GraphIRLayout(root=root, constraints=constraints)


def _find_root(graph: GraphIR) -> str:
    """Infer root from edges if not set in layout."""
    targets = {e.target for e in graph.edges}
    roots = [nid for nid in graph.nodes if nid not in targets]
    if len(roots) == 1:
        return roots[0]
    if not roots:
        raise ValueError("LayoutDerivationEngine: cannot infer root (all nodes have incoming edges)")
    raise ValueError(
        f"LayoutDerivationEngine: multiple root candidates: {roots}. "
        f"Set graph.layout.root explicitly."
    )
