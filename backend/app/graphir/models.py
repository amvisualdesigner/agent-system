"""GraphIR core models — single canonical IR for BI dashboards + FileOp.

FileOp is the output contract: every BackendRenderer produces a list
of FileOp that the pipeline returns to the caller.

GraphIR is the ONLY structural representation in the system.
Everything else is derived from it or discarded.

Construction lifecycle:
  1. GraphIRDraft (mutable, builder-internal, no invariants)
  2. .freeze() → GraphIR (immutable, fully validated, cross-boundary)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any


class EdgeRole(Enum):
    """Purely semantic composition role between parent and child.

    CRITICAL: EdgeRole describes ONLY the semantic relationship between
    components. It does NOT describe layout position, visual grouping,
    or UI arrangement.

    If a role name contains a position word (left, right, top, bottom,
    center, column, row), the design has regressed to slots.

    These three roles cover ALL composition semantics:
      - CONTAINS:  parent encloses child (generic composition)
      - PRIMARY:   child is the primary/dominant content
      - SUPPORTING: child is secondary/augmenting content
    """
    CONTAINS = "contains"
    PRIMARY = "primary"
    SUPPORTING = "supporting"


# Role constraint derivation rules (NOT part of EdgeRole definition):
#   PRIMARY children   → dominant visual area (largest space)
#   SUPPORTING children → secondary areas (sidebar, supplementary)
#   CONTAINS children   → default flow
#
# These rules live in LayoutDerivationEngine, NOT here.


class LayoutConstraint(Enum):
    """Abstract spatial constraints.

    Produced by LayoutDerivationEngine from GraphIR + metadata.
    NOT part of EdgeRole. NOT CSS.
    Each backend interprets these into framework-specific styling.
    """
    FULL_WIDTH = "full_width"
    HALF_WIDTH = "half_width"
    THIRD_WIDTH = "third_width"
    ROW = "row"
    COLUMN = "column"
    STACK = "stack"
    HIDDEN = "hidden"


@dataclass(frozen=True)
class GraphIRNode:
    """A single semantic component in the dashboard graph.

    Immutable after construction. Identity is the 'id' field.
    The 'type' field is a semantic component identifier (e.g. "KpiRow",
    "Timeseries", "AnalyticsTable"). It is NOT a file path, NOT a
    React component name. It is resolved to a renderer implementation
    at backend time.

    The 'data' field contains resolved business parameters only.
    No rendering metadata, no layout hints, no imports.

    The 'metadata' field contains SEMANTIC metadata ONLY:
      - priority: relative importance hint
      - domain: business domain tag
      - grouping: logical group identifier (NOT layout group)

    PROHIBITED in metadata:
      - import hints
      - component references (file paths, module names)
      - file_path hints
      - layout positions
      - framework-specific keys
    """
    id: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, 'data', _deep_freeze(self.data))
        object.__setattr__(self, 'metadata', _deep_freeze(self.metadata))


@dataclass(frozen=True)
class GraphIREdge:
    """A directed edge representing semantic composition.

    'source' is the parent node id.
    'target' is the child node id.
    'role' is a PURELY SEMANTIC EdgeRole.

    The graph MUST be a DAG with exactly one root (a node with
    no inbound edges). Enforced by GraphIRValidator at freeze().

    Composition is explicit: if a child has no edge, it does not
    exist in the graph. No binding step needed.
    """
    source: str
    target: str
    role: EdgeRole

    def __post_init__(self):
        if not isinstance(self.role, EdgeRole):
            raise ValueError(
                f"GraphIREdge role must be an EdgeRole enum value, "
                f"got '{self.role}' (type={type(self.role).__name__}). "
                f"Valid roles: {[r.value for r in EdgeRole]}"
            )


@dataclass(frozen=True)
class GraphIRLayout:
    """Spatial constraints derived from GraphIR by LayoutDerivationEngine.

    NOT part of GraphIR construction. Produced by a SEPARATE
    pipeline stage (layout.derive()). Each backend interprets
    constraints into framework-specific styling.

    'root' is the node id of the root container.
    'constraints' maps node id → list of LayoutConstraint.
    """
    root: str
    constraints: dict[str, list[LayoutConstraint]] = field(default_factory=dict)


def _deep_freeze(obj):
    """Recursively freeze dicts into MappingProxyType — protects nested data."""
    if isinstance(obj, dict):
        return MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return tuple(_deep_freeze(v) for v in obj)
    return obj


@dataclass(frozen=True)
class GraphIR:
    """Immutable, validated GraphIR.

    Can ONLY be produced by GraphIRDraft.freeze().
    Once constructed, no field can be modified.
    Enrichment produces new instances via pipeline steps.

    __post_init__ contains ONLY lightweight sanity checks.
    Heavy validation lives in GraphIRValidator.
    """
    nodes: dict[str, GraphIRNode]
    edges: list[GraphIREdge]
    layout: GraphIRLayout
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, 'nodes', _deep_freeze(self.nodes))
        assert isinstance(self.nodes, MappingProxyType), (
            "GraphIR.nodes must be deep-frozen. Use GraphIRDraft.freeze()."
        )
        assert isinstance(self.edges, list), "GraphIR.edges must be list"
        assert isinstance(self.layout, GraphIRLayout), "GraphIR.layout must be GraphIRLayout"
        assert all(isinstance(k, str) for k in self.nodes), "GraphIR.nodes keys must be strings"
        assert all(isinstance(e, GraphIREdge) for e in self.edges), "GraphIR.edges items must be GraphIREdge"


@dataclass
class GraphIRDraft:
    """Mutable working state for GraphIR construction.

    ONLY used inside builder.py. Never exported outside the builder module.

    Allows incremental construction:
      1. Add nodes (with or without edges)
      2. Add edges (with or without matching nodes yet)
      3. Remove nodes (cleans up associated edges)
      4. Inspect partial state (orphan nodes, inferred root)

    GraphIRDraft has NO invariants during construction. This is intentional:
    it makes the builder resilient to partial or ambiguous LLM output.

    The ONLY operation that enforces invariants is .freeze().
    """
    nodes: dict[str, GraphIRNode] = field(default_factory=dict)
    edges: list[GraphIREdge] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)

    def add_node(self, node: GraphIRNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: GraphIREdge) -> None:
        self.edges.append(edge)

    def remove_node(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)
        self.edges = [e for e in self.edges if e.source != node_id and e.target != node_id]

    def get_orphan_nodes(self) -> list[str]:
        """Nodes with no incoming edge and not root — useful for partial construction reporting."""
        targets = {e.target for e in self.edges}
        root = self._infer_root()
        candidates = [nid for nid in self.nodes if nid not in targets]
        if root is not None and root in candidates:
            candidates.remove(root)
        return candidates

    def _infer_root(self) -> str | None:
        targets = {e.target for e in self.edges}
        candidates = [nid for nid in self.nodes if nid not in targets]
        return candidates[0] if len(candidates) == 1 else None

    def freeze(self) -> GraphIR:
        """Produce immutable, validated GraphIR.

        This is the ONLY enforcement point for graph invariants.
        All checks run here:
          - All edges reference existing nodes
          - DAG (no cycles)
          - Exactly one root
          - All nodes reachable from root (inside is_dag)

        Raises ValueError with a specific message on any violation.
        """
        from app.graphir.validator import GraphIRValidator

        GraphIRValidator.check_edges_exist(self.nodes, self.edges)
        GraphIRValidator.is_dag(self.nodes, self.edges)
        root = GraphIRValidator.find_root(self.nodes, self.edges)

        layout = GraphIRLayout(root=root)

        return GraphIR(
            nodes=dict(self.nodes),
            edges=list(self.edges),
            layout=layout,
            params=dict(self.params),
        )


@dataclass
class FileOp:
    action: str
    path: str
    content: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        base = {"action": self.action, "path": self.path, "content": self.content}
        if self.metadata and "pipeline_route" in self.metadata:
            base["pipeline_route"] = self.metadata["pipeline_route"]
        return base


@dataclass
class GraphIRIntegrityResult:
    """Result of validating GraphIR structural integrity.

    NEVER raises — always returns a result. The pipeline reads
    .status and decides whether to halt or continue with degraded
    integrity.
    """
    status: str  # "ok" | "degraded"
    dangling_edges: list[dict] = field(default_factory=list)
    malformed_nodes: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_report(self) -> dict:
        return {
            "graph_integrity": self.status,
            "dangling_edges": self.dangling_edges,
            "malformed_nodes": self.malformed_nodes,
            "summary": self.summary,
        }


def validate_graph_integrity(graph: GraphIR) -> GraphIRIntegrityResult:
    """Validate GraphIR structural integrity without mutation.

    Checks:
      A. Dangling references — edges targeting non-existent node_ids
      B. Malformed nodes — null data, null metadata, missing type/id

    Returns GraphIRIntegrityResult — NEVER raises.
    """
    dangling_edges: list[dict] = []
    malformed_nodes: list[dict] = []

    # A. Dangling edges
    node_ids = set(graph.nodes.keys())
    for edge in graph.edges:
        if edge.source not in node_ids:
            dangling_edges.append({
                "reason": "missing_source",
                "source": edge.source,
                "target": edge.target,
                "role": edge.role.value,
            })
        if edge.target not in node_ids:
            dangling_edges.append({
                "reason": "missing_target",
                "source": edge.source,
                "target": edge.target,
                "role": edge.role.value,
            })

    # B. Malformed nodes
    for nid, node in graph.nodes.items():
        if node.data is None:
            malformed_nodes.append({
                "node_id": nid,
                "reason": "null_data",
            })
        elif not isinstance(node.data, (dict, MappingProxyType)):
            malformed_nodes.append({
                "node_id": nid,
                "reason": "invalid_data_type",
                "type": type(node.data).__name__,
            })
        if not node.type or not isinstance(node.type, str):
            malformed_nodes.append({
                "node_id": nid,
                "reason": "missing_or_invalid_type",
                "type": node.type,
            })
        if not node.id:
            malformed_nodes.append({
                "node_id": nid,
                "reason": "missing_id",
            })

    status = "degraded" if (dangling_edges or malformed_nodes) else "ok"
    summary = {
        "total_nodes": len(graph.nodes),
        "total_edges": len(graph.edges),
        "dangling_edge_count": len(dangling_edges),
        "malformed_node_count": len(malformed_nodes),
    }

    return GraphIRIntegrityResult(
        status=status,
        dangling_edges=dangling_edges,
        malformed_nodes=malformed_nodes,
        summary=summary,
    )
