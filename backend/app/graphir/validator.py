"""GraphIRValidator — explicit validation, not hidden in __post_init__.

Validation philosophy:
  - Lightweight sanity checks in GraphIR.__post_init__
  - Heavy structural validation in GraphIRValidator.validate()
  - Called explicitly by GraphIRDraft.freeze() and GraphIRPipeline
  - NOT run automatically at GraphIR construction time

This prevents the dataclass from becoming a "validation monolith"
and keeps validation logic explicit and testable.
"""

from __future__ import annotations

from typing import Any

from app.graphir.models import (
    EdgeRole,
    GraphIR,
    GraphIRNode,
    GraphIREdge,
    GraphIRLayout,
)


class GraphIRValidator:
    """Explicit validation for GraphIR structural invariants.

    All methods are static and pure (no side effects).
    Raise ValueError with descriptive messages on violation.
    """

    @staticmethod
    def find_root(nodes: dict[str, GraphIRNode], edges: list[GraphIREdge]) -> str:
        """Find the single root node (node with no inbound edges).

        Raises ValueError if there is not exactly one root.
        """
        targets = {e.target for e in edges}
        roots = [nid for nid in nodes if nid not in targets]
        if len(roots) != 1:
            raise ValueError(
                f"GraphIR: expected exactly 1 root, got {len(roots)}: {roots}"
            )
        return roots[0]

    @staticmethod
    def check_edges_exist(nodes: dict[str, GraphIRNode], edges: list[GraphIREdge]) -> None:
        """Verify all edge source/target values reference existing nodes."""
        for i, edge in enumerate(edges):
            if edge.source not in nodes:
                raise ValueError(
                    f"GraphIR: edges[{i}] source '{edge.source}' not in nodes. "
                    f"Available nodes: {list(nodes.keys())}"
                )
            if edge.target not in nodes:
                raise ValueError(
                    f"GraphIR: edges[{i}] target '{edge.target}' not in nodes. "
                    f"Available nodes: {list(nodes.keys())}"
                )

    @staticmethod
    def is_dag(nodes: dict[str, GraphIRNode], edges: list[GraphIREdge]) -> list[str]:
        """Verify the graph has no cycles.

        Performs DFS from each unvisited node. Returns topological
        order. Raises ValueError on cycle.

        Unlike find_root, this does NOT require exactly one root,
        so cycles are detected before root resolution.
        """
        if not nodes:
            raise ValueError("GraphIR: cannot validate DAG on empty node set")

        visited: set[str] = set()
        stack: set[str] = set()
        order: list[str] = []

        def _visit(nid: str, path: list[str] | None = None) -> None:
            if nid in stack:
                path_str = " → ".join(path + [nid]) if path else nid
                raise ValueError(
                    f"GraphIR: cycle detected: {path_str}"
                )
            if nid in visited or nid not in nodes:
                return
            stack.add(nid)
            for edge in edges:
                if edge.source == nid:
                    _visit(edge.target, (path or []) + [nid])
            stack.remove(nid)
            visited.add(nid)
            order.append(nid)

        for nid in nodes:
            if nid not in visited:
                _visit(nid)

        return order

    @staticmethod
    def validate(graph: GraphIR) -> None:
        """Full validation pass. Runs at pipeline end.

        Called by GraphIRPipeline.validate() after all enrichment
        and layout derivation steps.

        Lightweight type checks remain in GraphIR.__post_init__.
        Heavy structural checks live here.
        """
        if not isinstance(graph, GraphIR):
            raise ValueError("GraphIRValidator.validate() requires a GraphIR instance")

        GraphIRValidator.find_root(graph.nodes, graph.edges)
        GraphIRValidator.check_edges_exist(graph.nodes, graph.edges)
        GraphIRValidator.is_dag(graph.nodes, graph.edges)

        if not isinstance(graph.layout, GraphIRLayout):
            raise ValueError("GraphIR: layout must be a GraphIRLayout instance")

        if graph.layout.root not in graph.nodes:
            raise ValueError(
                f"GraphIR: layout root '{graph.layout.root}' not in nodes. "
                f"Available nodes: {list(graph.nodes.keys())}"
            )

        for nid in graph.layout.constraints:
            if nid not in graph.nodes:
                raise ValueError(
                    f"GraphIR: constraint node '{nid}' not in nodes. "
                    f"Available nodes: {list(graph.nodes.keys())}"
                )

    @staticmethod
    def check_edge_role_purity(edges: list[GraphIREdge]) -> None:
        """Hard constraint: EdgeRole must not contain position words.

        This check enforces the architectural rule that EdgeRole
        is purely semantic (CONTAINS/PRIMARY/SUPPORTING only).
        Any position-based role is a regression to slots.
        """
        position_words = {"left", "right", "top", "bottom", "center", "header", "footer",
                          "sidebar", "column", "row", "grid", "flex", "card", "panel",
                          "section", "group", "metrics", "chart", "table"}

        valid_roles = {EdgeRole.CONTAINS, EdgeRole.PRIMARY, EdgeRole.SUPPORTING}

        for i, edge in enumerate(edges):
            if edge.role not in valid_roles:
                role_name = edge.role.value if isinstance(edge.role, EdgeRole) else str(edge.role)
                if any(pos in role_name.lower() for pos in position_words):
                    raise ValueError(
                        f"EDGE ROLE REGRESSION: edges[{i}] has role '{role_name}' "
                        f"which contains a position/layout word. "
                        f"EdgeRole must be purely semantic (CONTAINS/PRIMARY/SUPPORTING). "
                        f"This is likely a slot reintroduction."
                    )
                raise ValueError(
                    f"GraphIR: edges[{i}] has invalid role '{role_name}'. "
                    f"Valid roles: {[r.value for r in valid_roles]}"
                )
