"""GraphIR Builder — StructuralIR → GraphIR via GraphIRDraft.

Single entry point: build_from_structural(ir). 1:1 capability→node,
no binding redistribution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.engine.errors import AmbiguousStructuralTargetError
from app.graphir.intent import (
    Intent,
    is_capability_metadata,
    make_intent_id,
    resolve_graphir_type_from_capability,
    resolve_edge_role_from_capability,
)
from app.graphir.models import (
    EdgeRole,
    GraphIR,
    GraphIRNode,
    GraphIREdge,
    GraphIRDraft,
)

if TYPE_CHECKING:
    from app.engine.structural_completion import (
        CompletionMode,
        StructuralIR,
    )
    from app.graphir.structure.models import StructuralResolution


class GraphIRBuilder:
    """PURE function: IntentPlan → GraphIR.

    Stateless, deterministic, no side effects.
    Orchestrates GraphIRDraft construction and freeze.
    """

    _ROLE_MAP: dict[str, EdgeRole] = {
        "PRIMARY": EdgeRole.PRIMARY,
        "SUPPORTING": EdgeRole.SUPPORTING,
        "CONTAINS": EdgeRole.CONTAINS,
    }

    # ── Phase 2: Node materialization helpers ──────────────────────

    @classmethod
    def _node_id_for_capability(
        cls, graphir_type: str, instance_id: str,
    ) -> str:
        """Stable node identity from instance_id.

        node_id = graphir_type:instance_id

        instance_id es proporcionado exclusivamente por el Resolver.
        El Builder NO deriva ni elige instance_id.
        """
        return f"{graphir_type}:{instance_id}"

    @classmethod
    def _add_intent_node(
        cls,
        draft: GraphIRDraft,
        intent: Intent,
        resolution: StructuralResolution | None = None,
    ) -> None:
        """Phase 2: Register a single intent as node or metadata.

        No edges, no root logic. Pure inventory stage.
        instance_id se obtiene exclusivamente de resolution.instance_mapping.
        El Builder NUNCA deriva ni elige instance_id.
        """
        if is_capability_metadata(intent.capability):
            cls._apply_metadata(draft, intent)
            return

        graphir_type = cls._resolve_graphir_type(intent, 0)
        instance_id = (
            resolution.instance_mapping.get(intent.capability)
            if resolution is not None and resolution.instance_mapping
            else "0"
        )
        node_id = cls._node_id_for_capability(graphir_type, instance_id)

        component_instance_path = (
            resolution.capability_to_path.get(intent.capability)
            if resolution is not None
            else None
        )

        node = GraphIRNode(
            id=node_id,
            type=graphir_type,
            component_instance_path=component_instance_path,
            data=dict(intent.params),
            metadata={
                "intent_id": intent.id,
                "intent_capability": intent.capability,
                "intent_source": intent.source,
            },
        )
        draft.add_node(node)

    @staticmethod
    def _apply_metadata(draft: GraphIRDraft, intent: Intent) -> None:
        """Apply metadata-only intents (domain, style, layout.grid/container) to draft params."""
        cap = intent.capability
        if cap.startswith("domain."):
            domains = draft.params.setdefault("domains", [])
            domain_name = cap.split(".", 1)[1]
            if domain_name not in domains:
                domains.append(domain_name)
        elif cap.startswith("style."):
            theme_key = cap.replace("style.", "").replace(".", "_")
            draft.params.setdefault("style_hints", {})[theme_key] = True
        elif cap.startswith("layout."):
            layout_key = cap.replace("layout.", "")
            draft.params.setdefault("layout_hints", {})[layout_key] = True

    @classmethod
    def _resolve_graphir_type(cls, intent: Intent, index: int) -> str:
        """Resolve graphir_type from intent, raising ValueError on failure."""
        graphir_type = resolve_graphir_type_from_capability(intent.capability)

        if graphir_type is None:
            raise ValueError(
                f"GraphIRBuilder: cannot resolve graphir_type for "
                f"intent '{intent.capability}' at index {index}"
            )
        return graphir_type

    # ── Phase 3: Root election ─────────────────────────────────────

    @staticmethod
    def _select_root(draft: GraphIRDraft) -> str:
        """Elect container root using semantic policy.

        Rules (in order):
          1. layout.page (type="Page") wins — structural container
          2. Fallback: first registered node (legacy behavior)

        Raises:
            AmbiguousStructuralTargetError: if draft is empty (no nodes
                to elect a root from — all operations were DELETE/KEEP).
        """
        if not draft.nodes:
            raise AmbiguousStructuralTargetError(
                "GraphIRBuilder: cannot select root from empty draft — "
                "no structural target was resolved. All operations were "
                "DELETE/KEEP or no capabilities produced nodes."
            )

        for nid, node in draft.nodes.items():
            if node.type == "Page":
                return nid

        return next(iter(draft.nodes))

    # ── Phase 4: Edge construction helpers ─────────────────────────

    @classmethod
    def _add_intent_edge(
        cls,
        draft: GraphIRDraft,
        intent: Intent,
        root_id: str,
        resolution: StructuralResolution | None = None,
    ) -> None:
        """Phase 4: Create edge from root to node. Skip metadata and root itself."""
        if is_capability_metadata(intent.capability):
            return

        graphir_type = cls._resolve_graphir_type(intent, 0)
        instance_id = (
            resolution.instance_mapping.get(intent.capability)
            if resolution is not None and resolution.instance_mapping
            else "0"
        )
        node_id = cls._node_id_for_capability(graphir_type, instance_id)

        if node_id == root_id:
            return

        role_name = resolve_edge_role_from_capability(intent.capability)

        if role_name is None:
            role = EdgeRole.CONTAINS
        else:
            role = cls._ROLE_MAP.get(role_name, EdgeRole.CONTAINS)

        draft.add_edge(GraphIREdge(
            source=root_id,
            target=node_id,
            role=role,
        ))

    # ── Public builder ─────────────────────────────────────────────

    @classmethod
    def build_from_structural(
        cls,
        ir: StructuralIR,
        resolution: StructuralResolution | None = None,
    ) -> GraphIR:
        """Apply StructuralIR operations → GraphIR (diff plan execution).

        StructuralIR NO es un "estado final deseado". Es un plan de
        operaciones (CREATE/MODIFY/DELETE/KEEP) que GraphIR APLICA:

          CREATE  → add new node
          MODIFY  → add node with updated params (identity estable por capability)
          DELETE  → skip (el nodo existente se elimina externamente)
          KEEP    → skip (el nodo existente permanece igual)

        NO hay reconstrucción — solo aplicación de operaciones.

        Args:
            ir: StructuralIR con operations explícitas.
            resolution: StructuralResolution opcional del resolver.
                        Si se provee, enriquece component_instance_path
                        con paths resueltos del registry.

        Returns:
            Frozen GraphIR con solo nodos CREATE + MODIFY.

        Raises:
            OperationError: if operation plan violates spec invariants.
            AmbiguousStructuralTargetError: if draft is empty (no CREATE/MODIFY ops).
            ValueError: if graph invariants fail or capability type unresolvable.
        """
        draft = GraphIRDraft()
        draft.params = {}

        from app.engine.structural_completion import CREATE, MODIFY

        intents: list[Intent] = []
        for op in ir.operations:
            action = op["action"]
            target = op["target"]
            payload = op.get("payload", {})

            if action not in (CREATE, MODIFY):
                continue

            intent = Intent(
                id=make_intent_id(f"op:{action}:{target}", target, "structural"),
                capability=target,
                params=dict(payload),
                source=f"structural_{action.lower()}",
            )
            intents.append(intent)

        for intent in intents:
            cls._add_intent_node(draft, intent, resolution=resolution)

        root_id = cls._select_root(draft)

        for intent in intents:
            cls._add_intent_edge(draft, intent, root_id, resolution=resolution)

        orphans = draft.get_orphan_nodes()
        if orphans:
            for nid in orphans:
                draft.add_edge(GraphIREdge(
                    source=root_id,
                    target=nid,
                    role=EdgeRole.CONTAINS,
                ))

        return draft.freeze()

def build_from_structural(ir: StructuralIR) -> GraphIR:
    """Convenience wrapper."""
    return GraphIRBuilder.build_from_structural(ir)
