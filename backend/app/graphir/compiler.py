"""UIIRCompiler — GraphIR → UIComponentTree.

Deterministic, no-loss transformation. Single source of truth for rendering.

Rules:
  - props = dict(node.data) — shallow copy, no filter
  - NO schema pruning, NO default injection, NO conditional logic
  - GraphIR edges → UIComponentNode.children (identical topology)
  - LayoutConstraint → UIComponentNode.layout_hints

Invariant enforced at construction:
  forall node in GraphIR:
    node.data.keys() ⊆ UIComponentNode.props.keys()
"""

from __future__ import annotations

from app.graphir.models import GraphIR, GraphIRLayout
from app.graphir.ui_ir import UIComponentNode, UIComponentTree


class UIIRCompiler:
    """PURE function: GraphIR + GraphIRLayout → UIComponentTree.

    Stateless, deterministic, no side effects.
    The ONLY component that produces UIComponentTree from GraphIR.
    """

    @staticmethod
    def compile(graph: GraphIR, layout: GraphIRLayout) -> UIComponentTree:
        """Transform GraphIR into a framework-agnostic UI component tree.

        Args:
            graph: Validated GraphIR.
            layout: Derived GraphIRLayout.

        Returns:
            UIComponentTree with complete, unfiltered props.

        Raises:
            AssertionError: if any node.data key is lost during compilation.
        """
        children_map: dict[str, list[str]] = {}
        for edge in graph.edges:
            children_map.setdefault(edge.source, []).append(edge.target)

        root = UIIRCompiler._build_node(graph, layout, graph.layout.root, children_map)
        return UIComponentTree(root=root)

    @classmethod
    def _build_node(
        cls,
        graph: GraphIR,
        layout: GraphIRLayout,
        node_id: str,
        children_map: dict[str, list[str]],
    ) -> UIComponentNode:
        node = graph.nodes[node_id]

        props = dict(node.data)

        # INVARIANTE: no-loss guarantee
        assert set(node.data.keys()).issubset(set(props.keys())), (
            f"UI IR invariant violated: node.data keys lost for '{node_id}' "
            f"(type={node.type}). Missing: {set(node.data.keys()) - set(props.keys())}"
        )

        child_ids = children_map.get(node_id, [])
        children = [
            cls._build_node(graph, layout, cid, children_map)
            for cid in child_ids
            if cid in graph.nodes
        ]

        return UIComponentNode(
            id=node.id,
            component=node.type,
            props=props,
            children=children,
            layout_hints=layout.constraints.get(node_id, []),
        )
