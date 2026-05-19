"""GraphIR Debug View — introspection tool, NOT part of the pipeline.

Provides:
  - JSON serialization of GraphIR + Layout
  - DAG adjacency list
  - Layout constraint summary
  - Diagnostics (orphans, missing constraints, etc.)

Zero impact on rendering output. Development/debug only.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.graphir.models import GraphIR, GraphIRLayout, GraphIREdge


def visualize(graph: GraphIR, layout: GraphIRLayout | None = None) -> dict:
    """Produce a debuggable representation of GraphIR + layout.

    Args:
        graph: A frozen GraphIR.
        layout: Optional GraphIRLayout. If not provided, uses graph.layout.

    Returns:
        Dict with JSON, adjacency list, diagnostics, and layout summary.
    """
    target_layout = layout or graph.layout

    return {
        "graph": _serialize_graph(graph),
        "layout": _serialize_layout(target_layout),
        "dag_edges": _dag_adjacency_list(graph.edges),
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "root": target_layout.root,
        "constraints_per_node": _constraints_summary(target_layout),
        "diagnostics": _diagnose(graph, target_layout),
    }


def _serialize_graph(graph: GraphIR) -> dict:
    base = {
        "nodes": {
            nid: {
                "id": node.id,
                "type": node.type,
                "data": dict(node.data),
                "metadata": dict(node.metadata),
            }
            for nid, node in graph.nodes.items()
        },
        "edges": [
            {
                "source": e.source,
                "target": e.target,
                "role": e.role.value,
            }
            for e in graph.edges
        ],
        "params": dict(graph.params),
    }
    if graph.layout:
        base["layout"] = {
            "root": graph.layout.root,
            "constraints": {
                nid: [c.value for c in constraints]
                for nid, constraints in graph.layout.constraints.items()
            },
        }
    return base


def _serialize_layout(layout: GraphIRLayout) -> dict:
    return {
        "root": layout.root,
        "constraints": {
            nid: [c.value for c in constraints]
            for nid, constraints in layout.constraints.items()
        },
    }


def _dag_adjacency_list(edges: list[GraphIREdge]) -> dict:
    adj: dict[str, list[dict]] = {}
    for edge in edges:
        adj.setdefault(edge.source, []).append({
            "target": edge.target,
            "role": edge.role.value,
        })
    return adj


def _constraints_summary(layout: GraphIRLayout) -> dict:
    return {
        nid: [c.value for c in constraints]
        for nid, constraints in layout.constraints.items()
    }


def _diagnose(graph: GraphIR, layout: GraphIRLayout) -> list[str]:
    """Run diagnostics to find potential issues in the graph.

    Returns a list of human-readable issue descriptions.
    Empty list = no issues detected.
    """
    issues: list[str] = []

    for nid in graph.nodes:
        if nid not in layout.constraints:
            issues.append(f"node '{nid}' has no layout constraint")

    targets = {e.target for e in graph.edges}
    for nid in graph.nodes:
        if nid != layout.root and nid not in targets:
            issues.append(f"node '{nid}' is orphan (not in any edge and not root)")

    return issues
