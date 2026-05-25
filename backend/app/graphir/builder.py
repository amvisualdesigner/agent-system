"""GraphIR Builder — StructuralIR → GraphIR via GraphIRDraft.

Single entry point: build_from_structural(ir). 1:1 capability→node,
no binding redistribution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    def _node_id_for_capability(cls, graphir_type: str, capability: str) -> str:
        """Stable node identity from capability name, not positional index.

        La misma capability produce siempre el mismo node_id,
        independientemente de qué otras capabilities haya en la lista.
        Esto evita que DELETE/MODIFY/KEEP cambie identidades.

        Para metadata capabilities (domain, style, layout) no se crean nodos.
        """
        # Page es caso especial por ser root
        if graphir_type == "Page":
            return graphir_type
        # Para el resto, el type es único por capability en el structural path
        return graphir_type

    @classmethod
    def _add_intent_node(
        cls,
        draft: GraphIRDraft,
        intent: Intent,
    ) -> None:
        """Phase 2: Register a single intent as node or metadata.

        No edges, no root logic. Pure inventory stage.
        Identity estable por capability, no por posición en lista.
        """
        if is_capability_metadata(intent.capability):
            cls._apply_metadata(draft, intent)
            return

        graphir_type = cls._resolve_graphir_type(intent, 0)
        node_id = cls._node_id_for_capability(graphir_type, intent.capability)

        node = GraphIRNode(
            id=node_id,
            type=graphir_type,
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
        """
        if not draft.nodes:
            raise ValueError("GraphIRBuilder: cannot select root from empty draft")

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
    ) -> None:
        """Phase 4: Create edge from root to node. Skip metadata and root itself."""
        if is_capability_metadata(intent.capability):
            return

        graphir_type = cls._resolve_graphir_type(intent, 0)
        node_id = cls._node_id_for_capability(graphir_type, intent.capability)

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
        repo_state: set[str] | None = None,
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
            repo_state: Conjunto de capability names existentes.
                        Si se provee, valida las operaciones antes de aplicarlas.

        Returns:
            Frozen GraphIR con solo nodos CREATE + MODIFY.

        Raises:
            OperationError: if operation plan violates spec invariants.
            ValueError: if graph invariants fail or capability type unresolvable.
        """
        from app.engine.structural_completion import (
            CREATE, MODIFY, validate_operations, OperationError,
        )

        # Validate operations against repo_state before applying
        op_warnings = validate_operations(ir.operations, repo_state)
        if op_warnings:
            import logging
            logging.getLogger(__name__).warning(
                "Operation warnings: %s", op_warnings,
            )

        draft = GraphIRDraft()
        draft.params = {}

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
            cls._add_intent_node(draft, intent)

        root_id = cls._select_root(draft)

        for intent in intents:
            cls._add_intent_edge(draft, intent, root_id)

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
