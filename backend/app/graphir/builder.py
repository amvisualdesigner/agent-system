"""GraphIR Builder — IntentPlan → GraphIR via GraphIRDraft.

Converts semantic intent plans into validated, frozen GraphIR.
The builder is the ONLY component that produces GraphIR from IntentPlan.

Supports both Intent (new) and IntentNode (legacy/deprecated).
"""

from __future__ import annotations

from typing import Any

from app.graphir.intent import (
    Intent,
    IntentExtensionRegistry,
    IntentNode,
    IntentPlan,
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

    @classmethod
    def build(cls, plan: IntentPlan) -> GraphIR:
        """Convert an IntentPlan into a validated, frozen GraphIR.

        Steps:
          1. Validate IntentPlan
          2. Create root node from first intent
          3. Add remaining intents as children with inferred edge roles
          4. Auto-attach orphan nodes
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

        for i, intent in enumerate(plan.intents):
            cls._add_intent_to_draft(draft, intent, i, plan)

        orphans = draft.get_orphan_nodes()
        if orphans and draft._infer_root() is not None:
            root = draft._infer_root()
            for nid in orphans:
                draft.add_edge(GraphIREdge(
                    source=root,
                    target=nid,
                    role=EdgeRole.CONTAINS,
                ))

        return draft.freeze()

    @classmethod
    def _add_intent_to_draft(
        cls,
        draft: GraphIRDraft,
        intent: Intent | IntentNode,
        index: int,
        plan: IntentPlan,
    ) -> None:
        if isinstance(intent, Intent):
            graphir_type = resolve_graphir_type_from_capability(intent.capability)
            if graphir_type is None:
                graphir_type = cls._legacy_resolve(intent)
        else:
            graphir_type = IntentExtensionRegistry.resolve_graphir_type(intent.type)

        if graphir_type is None:
            raise ValueError(
                f"GraphIRBuilder: cannot resolve graphir_type for "
                f"intent '{getattr(intent, 'capability', intent.type)}' at index {index}"
            )

        node_id = f"{graphir_type}_{index}" if index > 0 else graphir_type

        metadata: dict[str, Any] = {"intent_type": intent.type if isinstance(intent, IntentNode) else ""}
        if isinstance(intent, Intent):
            metadata["intent_id"] = intent.id
            metadata["intent_capability"] = intent.capability
            metadata["intent_source"] = "contract"

        node = GraphIRNode(
            id=node_id,
            type=graphir_type,
            data=dict(intent.params),
            metadata=metadata,
        )
        draft.add_node(node)

        if index == 0:
            draft.params = dict(plan.params)
            return

        if isinstance(intent, Intent):
            role_name = resolve_edge_role_from_capability(intent.capability)
        else:
            role_name = IntentExtensionRegistry.resolve_edge_role(intent.type)

        if role_name is None:
            role = EdgeRole.CONTAINS
        else:
            role = cls._ROLE_MAP.get(role_name, EdgeRole.CONTAINS)

        root_id = cls._find_first_node_id(draft)
        draft.add_edge(GraphIREdge(
            source=root_id,
            target=node_id,
            role=role,
        ))

    @classmethod
    def _legacy_resolve(cls, intent: Intent) -> str | None:
        """Fallback: try to resolve via IntentExtensionRegistry."""
        # Map capability → IntentType name for legacy resolution
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

    @staticmethod
    def _find_first_node_id(draft: GraphIRDraft) -> str:
        for nid in draft.nodes:
            return nid
        raise ValueError("GraphIRBuilder: draft has no nodes")


def build_from_plan(plan: IntentPlan) -> GraphIR:
    """Convenience wrapper."""
    return GraphIRBuilder.build(plan)
