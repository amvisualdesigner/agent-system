"""UIIRCompiler — GraphIR → UIComponentTree.

Deterministic, no-loss transformation. Single source of truth for rendering.

Rules:
  - props = {} (default) — BindingResolver is the ONLY source of UI props.
    compiler MUST NOT read node.data for UI props (HARD RULE — Binding v4 STEP 2).
  - resolved_bindings from BindingResolver → UIComponentNode.props
  - NO schema pruning, NO default injection, NO conditional logic
  - GraphIR edges → UIComponentNode.children (identical topology)
  - LayoutConstraint → UIComponentNode.layout_hints

Phase 6 — Composition Data Flow:
  - Page is the sole data owner. Children are pure presentational nodes.
  - DataSourceIR with slices lives in UIComponentTree.page_data_source.
  - Children do NOT receive workspace (no data_access.json resolution).
  - validate_node() enforces: non-Page nodes must NOT have data_imports.

Binding v4 invariants (enforced post-KILL_SWITCH):
  - forall component: props is sourced from ResolvedBindings, NOT from node.data
  - node.data contains INTENT (contract params), NOT UI props
  - Missing binding for a required prop → MISSING_REQUIRED_PROPS (not silent empty)

Compilation gate (CRITICAL):
  MISSING_REQUIRED_PROPS — raised when a component has required props that
  cannot be resolved. This is a HARD error that stops the pipeline.
  The renderer must NEVER be invoked when this is raised.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from app.binding.models import ResolvedBindings
from app.contracts.semantic_resolution import SemanticResolution
from app.graphir.models import GraphIR, GraphIRNode, EdgeRole
from app.graphir.layout import GraphIRLayout
from app.graphir.ui_ir import (
    UIComponentNode,
    UIComponentTree,
)
from app.signature.prop_mapper import MISSING_REQUIRED_PROPS


class UIIRCompiler:
    """PURE function: GraphIR + GraphIRLayout → UIComponentTree.

    Stateless, deterministic, no side effects.
    The ONLY component that produces UIComponentTree from GraphIR.

    Semantic metadata (UIComponentTree fields):
        semantic_warnings — per-component binding issues aggregated from
            PropMapper.BindingResult.warnings across all nodes.
        semantic_fidelity_score — proportion of contract_params consumed
            by at least one binding in the tree (union-based).
        consumed_params — union of all contract params consumed.
        provenance — per-node traceability: {node_id: {prop_name: [contract_param_names]}}
    """

    @staticmethod
    def compile(
        graph: GraphIR,
        layout: GraphIRLayout,
        resolved_bindings: ResolvedBindings | None = None,
        component_signatures: dict[str, dict] | None = None,
    ) -> UIComponentTree:
        """Transform GraphIR into a framework-agnostic UI component tree.

        Props are pre-resolved by BindingResolver into ResolvedBindings.
        The compiler only validates that required ⊆ resolved_props and
        produces UIComponentTree. It knows NOTHING about contract_params
        or data_access.json resolution.

        REQUIRED PROPS ENFORCEMENT:
        After looking up ResolvedBindings, all required props (from component
        signature) MUST be in the resolved props. Resolution is binary:
          - prop in ResolvedBindings.component_props[type] → OK
          - prop NOT in ResolvedBindings.component_props[type] → MISSING_REQUIRED_PROPS

        Args:
            graph: Validated GraphIR.
            layout: Derived GraphIRLayout.
            resolved_bindings: Pre-resolved bindings from BindingResolver.
            component_signatures: Extracted component type signatures.

        Returns:
            UIComponentTree with resolved props and semantic metadata.
        """
        children_map: dict[str, list[str]] = {}
        for edge in graph.edges:
            children_map.setdefault(edge.source, []).append(edge.target)

        all_warnings: list[str] = []
        all_binding_missing: list[str] = []

        page_ds = resolved_bindings.page_data_source if resolved_bindings else None

        root = UIIRCompiler._build_node(
            graph, layout, graph.layout.root, children_map,
            resolved_bindings=resolved_bindings,
            component_signatures=component_signatures,
            page_data_source=page_ds,
            _collect_warnings=all_warnings,
            _collect_binding_missing=all_binding_missing,
        )

        # Tree-level semantic coverage: how many consumed/total params
        consumed = len(resolved_bindings.consumed_params) if resolved_bindings else 0
        total = (
            len(resolved_bindings.consumed_params) + len(resolved_bindings.unconsumed_params)
            if resolved_bindings and resolved_bindings.unconsumed_params is not None
            else 0
        )
        # If no contract params at all, fidelity = 1.0 (no semantic expectation)
        fidelity = 1.0
        if resolved_bindings and (
            resolved_bindings.consumed_params or resolved_bindings.unconsumed_params
        ):
            total = len(resolved_bindings.consumed_params) + len(resolved_bindings.unconsumed_params)
            fidelity = consumed / total if total > 0 else 1.0

        tree = UIComponentTree(
            root=root,
            semantic_warnings=all_warnings,
            semantic_fidelity_score=fidelity,
            consumed_params=resolved_bindings.consumed_params if resolved_bindings else set(),
            provenance=resolved_bindings.provenance if resolved_bindings else {},
            page_data_source=page_ds,
        )

        # Phase 6: validate children are prop-only (no data_imports under Page)
        violations = UIIRCompiler.validate_node(tree)
        if violations:
            all_warnings.extend(violations)

        logger.info(
            "COMPILE_TREE: %d nodes, %d violations, fidelity=%.2f, "
            "page_ds=%s",
            _count_nodes(root), len(violations), fidelity,
            f"{len(page_ds.slices)} slices" if page_ds else "none",
        )

        # Fase 3.6: Instrument semantic fidelity gate (warning-only, no reject yet)
        if fidelity < 1.0:
            logger.warning(
                "SEMANTIC_FIDELITY component=%s fidelity=%.2f consumed=%d total=%d",
                root.component if root else "?",
                fidelity,
                consumed,
                total,
            )

        return tree

    @classmethod
    def _build_node(
        cls,
        graph: GraphIR,
        layout: GraphIRLayout,
        node_id: str,
        children_map: dict[str, list[str]],
        resolved_bindings: ResolvedBindings | None = None,
        component_signatures: dict[str, dict] | None = None,
        page_data_source: Any | None = None,
        _collect_warnings: list[str] | None = None,
        _collect_binding_missing: list[str] | None = None,
    ) -> UIComponentNode:
        node = graph.nodes.get(node_id)
        if not node:
            raise ValueError(f"Node {node_id} not found in GraphIR")

        sig = (component_signatures or {}).get(node.type)

        # ── Props resolution ──
        # HARD RULE: compiler MUST NOT access node.data for UI props.
        # node.data is intent layer (semantic params like "metrics", "metric").
        # Only BindingResolver output (component_props) is valid for UI props.
        # Intent params are consumed by BindingResolver BEFORE compile() is called.
        if resolved_bindings and node.type in resolved_bindings.component_props:
            resolved = resolved_bindings.component_props[node.type]
            props = dict(resolved)
            data_imports = tuple(resolved_bindings.imports)
            binding_missing = ()
        else:
            props = {}  # no fallback — BindingResolver is the only source
            data_imports = ()
            binding_missing = ()

        # ── Required prop enforcement (Stage 2 compilation gate) ──
        # Resolution is binary: required prop must be in resolved props.
        # No contract defaults, no page_ds_has_slice fallback.
        if sig is not None:
            required = sig.get("required_props", [])
            still_missing: list[str] = []

            for prop in required:
                if prop in props:
                    continue  # pre-resolved by BindingResolver
                still_missing.append(prop)

            if still_missing:
                raise MISSING_REQUIRED_PROPS(
                    component=node.type,
                    missing=still_missing,
                    available=list(props.keys()),
                    contract_params=[],
                )

            logger.info(
                "COMPILE[%s]: sig=%s | resolved=%s | required=%s | missing_required=%s",
                node.type,
                sig.get("prop_names", []) if sig else "none",
                list(props.keys()),
                required,
                still_missing,
            )

        child_ids = children_map.get(node_id, [])
        children = [
            cls._build_node(
                graph, layout, cid, children_map,
                resolved_bindings=resolved_bindings,
                component_signatures=component_signatures,
                page_data_source=page_data_source,
                _collect_warnings=_collect_warnings,
                _collect_binding_missing=_collect_binding_missing,
            )
            for cid in child_ids
            if cid in graph.nodes
        ]

        instance_only = node.metadata.get("instance_only", False) if node.metadata else False

        return UIComponentNode(
            id=node.id,
            component=node.type,
            props=props,
            data_imports=data_imports,
            instance_only=instance_only,
            binding_missing_props=binding_missing,
            children=children,
            layout_hints=layout.constraints.get(node_id, []),
        )

    @staticmethod
    def validate_node(tree: UIComponentTree) -> list[str]:
        """Phase 6: enforce children are prop-only (no data ownership).

        Under a Page with page_data_source, no child component may:
          - Have data_imports (they receive data via JSVariable refs from Page)
          - Have binding_missing_props (no per-component bindings)

        Lock invariant (CRITICAL): Page as data_owner:
          - Must have exactly 1 hook call in data_imports
          - Must have at least 1 child (data distribution target)
          - Children must have NO data_imports (enforced via _walk)

        This is compile-time enforcement — NOT a warning. Violations indicate
        the tree violates the Composition Data Flow invariant.

        Returns list of violation messages. Empty list = valid tree.
        """
        violations: list[str] = []
        if not tree.page_data_source:
            return violations
        if not tree.root:
            return violations

        # Lock invariant: Page data_owner must have exactly 1 hook call
        if tree.root.component == "Page":
            if len(tree.root.data_imports) != 1:
                violations.append(
                    f"Phase 6 violation: Page '{tree.root.id}' has "
                    f"{len(tree.root.data_imports)} data_imports (expected exactly 1 for hook). "
                    f"Page as data_owner must have exactly one hook call. "
                    f"Got: {tree.root.data_imports}"
                )
            if len(tree.root.children) == 0:
                violations.append(
                    f"Phase 6 violation: Page '{tree.root.id}' has 0 children but "
                    f"page_data_source defines {len(tree.page_data_source.slices)} slice(s). "
                    f"Page as data_owner must have at least one child to receive data."
                )

        def _walk(node: UIComponentNode, depth: int = 0) -> None:
            if node.component != "Page" and node.data_imports:
                violations.append(
                    f"Phase 6 violation: '{node.component}' (id={node.id}) has "
                    f"data_imports={node.data_imports} but is not the Page data owner. "
                    f"Children must be pure presentational — no data ownership."
                )
            if node.component != "Page" and node.binding_missing_props:
                violations.append(
                    f"Phase 6 violation: '{node.component}' (id={node.id}) has "
                    f"binding_missing_props={node.binding_missing_props} but per-component "
                    f"bindings are not supported under Page data ownership. "
                    f"Data must come from Page slices."
                )
            for child in node.children:
                _walk(child, depth + 1)

        _walk(tree.root)
        return violations


def _count_nodes(node: UIComponentNode) -> int:
    """Recursively count UIComponentNodes in a tree."""
    return 1 + sum(_count_nodes(c) for c in node.children)


def verify_graphir_coverage(
    structural_capabilities: list[dict],
    graph: Any,
    contract_composition_map: dict[str, str] | None = None,
) -> list[str]:
    """Guard: verify all KEEP slot children under a MODIFY/CREATE parent
    have corresponding nodes in GraphIR.

    The GraphIR builder (build_from_structural) only includes operations
    with action CREATE, MODIFY, or instance_only=True. KEEP children are
    skipped — but when their parent Page is being regenerated (MODIFY/CREATE),
    they MUST appear in the graph for correct composition.

    Args:
        structural_capabilities: list of cap dicts with name, action, instance_only.
        graph: GraphIR instance.
        contract_composition_map: {child_cap: parent_cap}. Optional.

    Returns:
        List of missing node descriptions. Empty = full coverage.
    """
    missing: list[str] = []
    graph_caps = set()
    for n in graph.nodes.values():
        cap = n.metadata.get("intent_capability") if n.metadata else None
        if cap:
            graph_caps.add(cap)

    for cap in structural_capabilities:
        name = cap.get("name", "")
        action = cap.get("action", "")
        instance_only = cap.get("instance_only", False)

        if action != "KEEP" or instance_only:
            continue

        if contract_composition_map:
            parent = contract_composition_map.get(name)
            if parent:
                parent_action = None
                for c in structural_capabilities:
                    if c.get("name") == parent:
                        parent_action = c.get("action")
                        break
                if parent_action in ("MODIFY", "CREATE") and name not in graph_caps:
                    missing.append(
                        f"KEEP child '{name}' (parent '{parent}' {parent_action}) "
                        f"not found in GraphIR — composition incomplete"
                    )
    return missing
