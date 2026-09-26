"""GraphIR — Graph-first Intermediate Representation for BI dashboards.

GraphIR is the SINGLE canonical IR of the system.
There is no other structural representation.

Architecture:
  IntentPlan (semantic, LLM-friendly)
       ↓
  GraphIR (structural, immutable, backend-agnostic)
       ↓
  LayoutDerivationEngine → GraphIRLayout
       ↓
  ConstraintGraph Renderer → list[FileOp]

Hard constraints (violation = regression):
  1. EdgeRole is purely semantic (CONTAINS/PRIMARY/SUPPORTING only)
  2. LayoutDerivationEngine is the ONLY layout authority
  3. GraphIRDraft is the ONLY mutable state (builder-internal)
  4. The renderer materializes only; it does not interpret semantic intent
  5. No template defines structure
"""

from app.graphir.models import (
    EdgeRole,
    LayoutConstraint,
    GraphIRNode,
    GraphIREdge,
    GraphIRLayout,
    GraphIR,
    GraphIRDraft,
)

from app.graphir.intent import (
    Intent,
    IntentPlan,
    make_intent_id,
    resolve_graphir_type_from_capability,
    resolve_edge_role_from_capability,
)

from app.graphir.validator import GraphIRValidator

from app.graphir.layout import LayoutDerivationEngine

from app.graphir.debug import visualize

from app.graphir.builder import GraphIRBuilder, build_from_structural

from app.graphir.pipeline import GraphIRPipeline, run_from_structural

__all__ = [
    "EdgeRole",
    "LayoutConstraint",
    "GraphIRNode",
    "GraphIREdge",
    "GraphIRLayout",
    "GraphIR",
    "GraphIRDraft",
    "Intent",
    "IntentPlan",
    "make_intent_id",
    "resolve_graphir_type_from_capability",
    "resolve_edge_role_from_capability",
    "GraphIRValidator",
    "LayoutDerivationEngine",
    "visualize",
    "GraphIRBuilder",
    "build_from_structural",
    "GraphIRPipeline",
    "run_from_structural",
]
