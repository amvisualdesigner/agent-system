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
    def _add_intent_node(
        cls,
        draft: GraphIRDraft,
        intent: Intent,
        index: int,
    ) -> None:
        """Phase 2: Register a single intent as node or metadata.

        No edges, no root logic. Pure inventory stage.
        """
        if is_capability_metadata(intent.capability):
            cls._apply_metadata(draft, intent)
            return

        graphir_type = cls._resolve_graphir_type(intent, index)

        node_id = f"{graphir_type}_{index}" if index > 0 else graphir_type

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
        index: int,
        root_id: str,
    ) -> None:
        """Phase 4: Create edge from root to node. Skip metadata and root itself."""
        if is_capability_metadata(intent.capability):
            return

        graphir_type = cls._resolve_graphir_type(intent, index)
        node_id = f"{graphir_type}_{index}" if index > 0 else graphir_type

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
    def build_from_structural(cls, ir: StructuralIR) -> GraphIR:
        """Convert StructuralIR → GraphIR (1:1, no binding redistribution).

        Structural IR ya tiene ownership resuelto. Esta construcción es
        una proyección directa: 1 capability → 1 node, sin
        bind_skillir_to_nodes ni validate_binding. NO hay redistribución
        de params ni re-interpretación semántica.

        Args:
            ir: StructuralIR con capabilities resueltas.

        Returns:
            Frozen GraphIR.

        Raises:
            ValueError: if graph invariants fail or capability type unresolvable.
        """
        from app.engine.structural_completion import CompletionMode

        draft = GraphIRDraft()
        draft.params = {}

        intents: list[Intent] = []
        for rc in ir.capabilities:
            if rc.mode == CompletionMode.SAFE_SKIP:
                continue
            intents.append(Intent(
                id=make_intent_id(f"structural:{rc.name}", rc.name, "structural"),
                capability=rc.name,
                params=dict(rc.params),
                source="structural_completion",
            ))

        for i, intent in enumerate(intents):
            cls._add_intent_node(draft, intent, i)

        root_id = cls._select_root(draft)

        for i, intent in enumerate(intents):
            cls._add_intent_edge(draft, intent, i, root_id)

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
