"""LayoutResolver — aplica layout_hints de StructuralIR al GraphIR.

NO interpreta intención. GraphIR solo recibe edges ya resueltos.
Lee anotaciones estructuradas de StructuralIR.layout_hints y modifica
edges de manera determinista y trazable.

Hints soportados:
  - move_after + scope="layout": reordena edge del target después del reference
  - move_after + scope="structure": (futuro) cambia parent del target
  - replace_anchor: edge del reemplazo hereda posición del reemplazado
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.graphir.models import GraphIR

if TYPE_CHECKING:
    from app.engine.structural_completion import StructuralIR


def resolve_layout(structural_ir: StructuralIR, graph: GraphIR) -> GraphIR:
    """Aplica layout_hints al graph. Retorna graph modificado (o igual si no hay hints).

    Determinista: mismo structural_ir + mismo graph → mismo output.
    GraphIR nunca contiene lógica de 'move semantics' — solo orden de edges ya resuelto.
    """
    if not structural_ir.layout_hints:
        return graph

    edges = list(graph.edges)
    modified = False

    for cap_target, hint in structural_ir.layout_hints.items():
        scope = hint.get("scope", "layout")

        # MOVE (layout scope): reordenar edge, no cambiar parent
        if "move_after" in hint and scope == "layout":
            ref_cap = hint["move_after"]
            target_node = _node_id_for_cap(graph, cap_target)
            ref_node = _node_id_for_cap(graph, ref_cap)
            if target_node and ref_node and target_node != ref_node:
                target_edges = [e for e in edges if e.target == target_node]
                other_edges = [e for e in edges if e.target != target_node]
                new_edges = []
                inserted = False
                for e in other_edges:
                    new_edges.append(e)
                    if e.target == ref_node and not inserted:
                        new_edges.extend(target_edges)
                        inserted = True
                if not inserted:
                    new_edges.extend(target_edges)
                edges = new_edges
                modified = True

        # REPLACE: edge del reemplazo hereda posición del reemplazado
        if "replace_anchor" in hint:
            old_cap = hint["replace_anchor"]
            old_node = _node_id_for_cap(graph, old_cap)
            target_node = _node_id_for_cap(graph, cap_target)
            if old_node and target_node and old_node != target_node:
                old_idx = next(
                    (i for i, e in enumerate(edges) if e.target == old_node),
                    None,
                )
                target_edges = [e for e in edges if e.target == target_node]
                other_edges = [e for e in edges if e.target != target_node]
                if old_idx is not None:
                    new_edges = []
                    inserted = False
                    for i, e in enumerate(other_edges):
                        new_edges.append(e)
                        if i == old_idx and not inserted:
                            new_edges.extend(target_edges)
                            inserted = True
                    if not inserted:
                        new_edges.extend(target_edges)
                    edges = new_edges
                    modified = True

    if not modified:
        return graph

    return GraphIR(
        nodes=graph.nodes,
        edges=edges,
        layout=graph.layout,
        params=graph.params,
    )


def _node_id_for_cap(graph: GraphIR, capability: str) -> str | None:
    """Busca node_id de una capability en el graph."""
    for nid, node in graph.nodes.items():
        meta = node.metadata or {}
        if meta.get("intent_capability") == capability:
            return nid
    return None
