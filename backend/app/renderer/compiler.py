"""Semantic UI IR Compiler — validation & contract enforcement layer.

Phase 6 additions:
  - validate_compiler_contract(ast, config, compiler_config)
  - validate_compiler_ir(root, compiler_config)
  - capture_ir_snapshot(root)
  - CompilerConfig, CompilerMode, CompilerIRSnapshot

Propagation Invariant:
  CompilerConfig must be passed verbatim across all pipeline boundaries.
  No cloning, no mutation, no recomputation.

Single Mode Authority Rule:
  The CompilerMode is defined uniquely in apply_engine.py and must be
  propagated explicitly to every subsystem that executes the pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Literal

from app.renderer.component_node import (
    ComponentNode,
    _collect_all_descendants,
    _extract_component_name,
)

logger = logging.getLogger(__name__)

CompilerMode = Literal["legacy", "strict", "enforced"]


@dataclass
class CompilerConfig:
    mode: CompilerMode

    def __post_init__(self):
        if self.mode not in ("legacy", "strict", "enforced"):
            raise ValueError(f"Invalid CompilerMode: {self.mode}")


@dataclass
class CompilerIRSnapshot:
    root_component: str
    node_count: int
    resolved_imports_state: dict[str, str | None]
    slot_bindings_state: dict[str, dict | None]
    slots_declared: dict[str, list[dict]]
    children_count: dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)


# ── Contract Gate (pre-pipeline) ──────────────────────────────────────


def validate_compiler_contract(
    ast: dict,
    renderer_config: dict,
    compiler_config: CompilerConfig,
) -> None:
    """Pre-pipeline validation of AST + renderer_config structural integrity.

    Validates INPUT shape only — no tree state, no derived state.
    Mode-dependent:
      - legacy:  SKIP (no validation)
      - strict:  basic shape checks
      - enforced: full structural contract enforcement, no fallback inputs

    Raises RuntimeError with 'ContractViolation:' prefix on any failure.
    """
    if compiler_config.mode == "legacy":
        return

    _validate_ast_shape(ast, renderer_config, compiler_config)
    _validate_config_shape(renderer_config, compiler_config)


def _validate_ast_shape(
    ast: dict,
    renderer_config: dict,
    config: CompilerConfig,
) -> None:
    nodes = ast.get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError("ContractViolation: AST missing 'nodes' list")

    if config.mode == "enforced":
        for i, node in enumerate(nodes):
            if not isinstance(node, dict):
                raise RuntimeError(
                    f"ContractViolation: AST node[{i}] is not a dict"
                )
            type_name = node.get("type")
            if not type_name:
                raise RuntimeError(
                    f"ContractViolation: AST node[{i}] missing 'type'"
                )
            raw_slots = node.get("slots")
            if raw_slots is not None:
                _validate_slot_specs(raw_slots, type_name)

        _detect_fallback_inputs(ast, renderer_config)


def _validate_slot_specs(raw_slots: list, node_type: str) -> None:
    if not isinstance(raw_slots, list):
        raise RuntimeError(
            f"ContractViolation: node '{node_type}' has invalid "
            f"'slots' (must be a list)"
        )
    for j, s in enumerate(raw_slots):
        if not isinstance(s, dict):
            raise RuntimeError(
                f"ContractViolation: slot[{j}] on node '{node_type}' "
                f"is not a dict"
            )
        if not s.get("allowed_types"):
            raise RuntimeError(
                f"ContractViolation: slot[{j}] on node '{node_type}' "
                f"missing 'allowed_types'"
            )


def _detect_fallback_inputs(ast: dict, renderer_config: dict) -> None:
    """Detect AST patterns that would trigger heuristic fallbacks
    in Phase 4/5 when enforced mode is active."""
    nodes = ast.get("nodes", [])
    files = renderer_config.get("files", [])

    if len(nodes) == 1 and files:
        root_path = files[0].get("path", "")
        root_name = _extract_component_name(root_path)
        only_slot_type = nodes[0].get("type", "")
        if root_name != only_slot_type:
            raise RuntimeError(
                f"ContractViolation: single-slot fallback not allowed in "
                f"enforced mode. Root component '{root_name}' does not "
                f"match slot type '{only_slot_type}'"
            )


def _validate_config_shape(
    renderer_config: dict,
    config: CompilerConfig,
) -> None:
    files = renderer_config.get("files")
    if not files:
        raise RuntimeError("ContractViolation: renderer_config missing 'files'")

    for i, f in enumerate(files):
        if not isinstance(f, dict):
            raise RuntimeError(
                f"ContractViolation: config file[{i}] is not a dict"
            )
        if "path" not in f:
            raise RuntimeError(
                f"ContractViolation: config file[{i}] missing 'path'"
            )


# ── IR Validation (post-enrichment, pre-emission) ─────────────────────


def validate_compiler_ir(
    root: ComponentNode,
    compiler_config: CompilerConfig,
) -> None:
    """Post-enrichment, pre-emission validation of compiler IR state.

    Validates DERIVED STATE only — no input shape checks.
    Mode-dependent:
      - legacy:  SKIP (no validation)
      - strict:  resolved_imports + slots/bindings consistency
      - enforced: strict + all children bound + no partial state

    Raises RuntimeError with 'IncompleteIR:' prefix on any failure.
    """
    if compiler_config.mode == "legacy":
        return

    for node in _collect_all_descendants(root):
        _validate_ir_node(node, compiler_config)


def _validate_ir_node(node: ComponentNode, config: CompilerConfig) -> None:
    if node.resolved_imports is None:
        raise RuntimeError(
            f"IncompleteIR: node '{node.component}' has unresolved imports. "
            f"Call resolve_imports(root) before emission."
        )

    if node.slots is not None:
        if node.slot_bindings is None:
            raise RuntimeError(
                f"IncompleteIR: node '{node.component}' has slots declared "
                f"but slot_bindings is None. "
                f"Call resolve_slots(root) before emission."
            )

        if config.mode == "enforced":
            _validate_slot_bindings_complete(node)
    else:
        if node.slot_bindings is not None:
            raise RuntimeError(
                f"IncompleteIR: node '{node.component}' has slot_bindings "
                f"but no slots declared. Consistency violation."
            )


def _validate_slot_bindings_complete(node: ComponentNode) -> None:
    spec_names = {s.name for s in node.slots}
    binding_names = set(node.slot_bindings.keys())

    if spec_names != binding_names:
        missing = spec_names - binding_names
        extra = binding_names - spec_names
        msg = f"IncompleteIR: node '{node.component}' slot/binding mismatch"
        if missing:
            msg += f" missing bindings: {missing}"
        if extra:
            msg += f" extra bindings: {extra}"
        raise RuntimeError(msg)

    all_bound: set[int] = set()
    for name, binding in node.slot_bindings.items():
        if isinstance(binding, list):
            for child in binding:
                for i, c in enumerate(node.children):
                    if c is child:
                        all_bound.add(i)
        else:
            for i, c in enumerate(node.children):
                if c is binding:
                    all_bound.add(i)

    for i, child in enumerate(node.children):
        if i not in all_bound:
            raise RuntimeError(
                f"IncompleteIR: unbound child '{child.component}' on "
                f"node '{node.component}' in enforced mode"
            )


# ── IR Snapshot (observation only, never validates) ───────────────────


def capture_ir_snapshot(root: ComponentNode) -> CompilerIRSnapshot:
    """Capture current IR state as an immutable snapshot.

    Observation only — never validates, never raises.
    Safe to call even on invalid IR (for debugging).

    IMPORTANT: Must be called AFTER resolve_imports + resolve_slots
    and BEFORE emit_tree. Calling earlier captures incomplete IR.
    The typical pipeline point is: resolve_slots → capture → emit_tree.
    """
    descs = _collect_all_descendants(root)

    resolved_state: dict[str, str | None] = {}
    slot_bindings_state: dict[str, dict | None] = {}
    slots_declared: dict[str, list[dict]] = {}
    children_count: dict[str, int] = {}

    for node in descs:
        resolved_state[node.component] = node.resolved_imports

        if node.slot_bindings is not None:
            summary: dict = {}
            for name, binding in node.slot_bindings.items():
                if isinstance(binding, list):
                    summary[name] = [c.component for c in binding]
                else:
                    summary[name] = binding.component
            slot_bindings_state[node.component] = summary
        else:
            slot_bindings_state[node.component] = None

        if node.slots is not None:
            slots_declared[node.component] = [
                {
                    "name": s.name,
                    "allowed_types": s.allowed_types,
                    "required": s.required,
                    "allowed": s.allowed,
                }
                for s in node.slots
            ]
        else:
            slots_declared[node.component] = []

        children_count[node.component] = len(node.children)

    return CompilerIRSnapshot(
        root_component=root.component,
        node_count=len(descs),
        resolved_imports_state=resolved_state,
        slot_bindings_state=slot_bindings_state,
        slots_declared=slots_declared,
        children_count=children_count,
    )
