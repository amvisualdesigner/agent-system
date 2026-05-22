"""UI IR — framework-agnostic component tree.

Sits between GraphIR and backend renderers. The ONLY rendering contract.
Backend renderers consume UIComponentTree, never GraphIR directly.

Architecture:
  GraphIR (structural, immutable)
    ↓ UIIRCompiler
  UIComponentTree (framework-agnostic)
    ↓ ReactBackend / VueBackend / HTMLBackend
  Final output
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UIComponentNode:
    """A single component in the framework-agnostic UI tree.

    Fields:
        id: Instance identity — GraphIRNode.id (unique per node).
        component: Render behavior identity — GraphIRNode.type (e.g. "AnalyticsTable").
        props: Framework-agnostic props — shallow copy of GraphIRNode.data.
        children: Child UIComponentNodes (derived from graph edges).
        layout_hints: LayoutConstraint list from LayoutDerivationEngine.
    """
    id: str
    component: str
    props: dict[str, Any]
    children: list[UIComponentNode] = field(default_factory=list)
    layout_hints: list[Any] = field(default_factory=list)


@dataclass
class UIComponentTree:
    """Complete framework-agnostic component tree.

    Produced by UIIRCompiler.compile(). Single source of truth for rendering.
    """
    root: UIComponentNode


@dataclass
class UIGeneratorContext:
    """Adapter between UIComponentNode and existing GraphIRNode-based generators.

    Has the same shape as GraphIRNode (.id, .type, .data, .metadata)
    so existing generators can consume it without changes.
    Prevents GraphIR leak into the UI IR pipeline.

    This adapter will be removed when generators are refactored to
    accept UIComponentNode directly.
    """
    id: str
    type: str
    data: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
