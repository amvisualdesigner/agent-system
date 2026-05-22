"""GraphIR Builder — IntentPlan → GraphIR via GraphIRDraft.

Converts semantic intent plans into validated, frozen GraphIR.
The builder is the ONLY component that produces GraphIR from IntentPlan.

Supports both Intent (new) and IntentNode (legacy/deprecated).

Construction phases (in order):
  1. Validate IntentPlan
  2. Node materialization — register nodes or apply metadata (pure inventory, no edges)
  3. Root election — select container root using semantic policy (layout.page > legacy fallback)
  4. Edge construction — bind all non-root nodes to elected root with semantic roles
  5. Freeze and return
"""

from __future__ import annotations

from typing import Any

from app.graphir.intent import (
    Intent,
    IntentExtensionRegistry,
    IntentNode,
    IntentPlan,
    is_graphir_node_capability,
    is_capability_metadata,
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
        intent: Intent | IntentNode,
        index: int,
    ) -> None:
        """Phase 2: Register a single intent as node or metadata.

        No edges, no root logic. Pure inventory stage.
        """
        if isinstance(intent, Intent) and is_capability_metadata(intent.capability):
            cls._apply_metadata(draft, intent)
            return

        graphir_type = cls._resolve_graphir_type(intent, index)

        node_id = f"{graphir_type}_{index}" if index > 0 else graphir_type

        metadata: dict[str, Any] = {"intent_type": intent.type if isinstance(intent, IntentNode) else ""}
        if isinstance(intent, Intent):
            metadata["intent_id"] = intent.id
            metadata["intent_capability"] = intent.capability
            metadata["intent_source"] = intent.source

        node = GraphIRNode(
            id=node_id,
            type=graphir_type,
            data=dict(intent.params),
            metadata=metadata,
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
    def _resolve_graphir_type(cls, intent: Intent | IntentNode, index: int) -> str:
        """Resolve graphir_type from intent, raising ValueError on failure."""
        if isinstance(intent, Intent):
            graphir_type = resolve_graphir_type_from_capability(intent.capability)
            if graphir_type is None:
                graphir_type = cls._legacy_resolve(intent)
        else:
            graphir_type = IntentExtensionRegistry.resolve_graphir_type(intent.type)

        if graphir_type is None:
            label = intent.capability if isinstance(intent, Intent) else intent.type
            raise ValueError(
                f"GraphIRBuilder: cannot resolve graphir_type for "
                f"intent '{label}' at index {index}"
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
        intent: Intent | IntentNode,
        index: int,
        root_id: str,
    ) -> None:
        """Phase 4: Create edge from root to node. Skip metadata and root itself."""
        if isinstance(intent, Intent) and is_capability_metadata(intent.capability):
            return

        graphir_type = cls._resolve_graphir_type(intent, index)
        node_id = f"{graphir_type}_{index}" if index > 0 else graphir_type

        if node_id == root_id:
            return

        if isinstance(intent, Intent):
            role_name = resolve_edge_role_from_capability(intent.capability)
        else:
            role_name = IntentExtensionRegistry.resolve_edge_role(intent.type)

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
    def build(cls, plan: IntentPlan) -> GraphIR:
        """Convert an IntentPlan into a validated, frozen GraphIR.

        Phases:
          1. Validate IntentPlan
          2. Node materialization — register nodes or apply metadata
          3. Root election — semantic policy (layout.page > legacy fallback)
          4. Edge construction — bind non-root nodes to elected root
          5. Freeze and return

        Args:
            plan: Validated IntentPlan.

        Returns:
            Frozen GraphIR.

        Raises:
            ValueError: if plan is invalid or graph invariants fail.
        """
        IntentPlan.validate(plan)

        draft = GraphIRDraft()
        draft.params = dict(plan.params)

        # ── Phase 2: Node materialization (inventory, no routing) ──
        for i, intent in enumerate(plan.intents):
            cls._add_intent_node(draft, intent, i)

        # ── Phase 3: Root election (semantic policy layer) ─────────
        root_id = cls._select_root(draft)

        # ── Phase 4: Edge construction (relationship binding) ──────
        for i, intent in enumerate(plan.intents):
            cls._add_intent_edge(draft, intent, i, root_id)

        # Safety net: attach any remaining orphans (backward compat)
        orphans = draft.get_orphan_nodes()
        if orphans:
            for nid in orphans:
                draft.add_edge(GraphIREdge(
                    source=root_id,
                    target=nid,
                    role=EdgeRole.CONTAINS,
                ))

        return draft.freeze()

    # ── Legacy resolvers ──────────────────────────────────────────

    @classmethod
    def _legacy_resolve(cls, intent: Intent) -> str | None:
        """Fallback: try to resolve via IntentExtensionRegistry."""
        capability_to_type = {
            "display.kpi_row": "KPIGROUP",
            "display.timeseries": "CHART",
            "display.analytics_table": "DATATABLE",
            "display.filter_panel": "FILTERPANEL",
            "embed.external": "EMBED",
            "layout.page": "PAGE",
        }
        type_name = capability_to_type.get(intent.capability)
        if type_name is None:
            return None
        return IntentExtensionRegistry.resolve_graphir_type(type_name)


def build_from_plan(plan: IntentPlan) -> GraphIR:
    """Convenience wrapper."""
    return GraphIRBuilder.build(plan)
