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
  BackendRenderer → list[FileOp]

Hard constraints (violation = regression):
  1. EdgeRole is purely semantic (CONTAINS/PRIMARY/SUPPORTING only)
  2. LayoutDerivationEngine is the ONLY layout authority
  3. GraphIRDraft is the ONLY mutable state (builder-internal)
  4. BackendRenderer is stateless and dumb
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
    IntentType,
    IntentExtensionRegistry,
    IntentNode,
    IntentPlan,
)

from app.graphir.validator import GraphIRValidator

from app.graphir.layout import LayoutDerivationEngine

from app.graphir.debug import visualize

from app.graphir.builder import GraphIRBuilder, build_from_plan

from app.graphir.pipeline import GraphIRPipeline, run_pipeline

__all__ = [
    "EdgeRole",
    "LayoutConstraint",
    "GraphIRNode",
    "GraphIREdge",
    "GraphIRLayout",
    "GraphIR",
    "GraphIRDraft",
    "IntentType",
    "IntentExtensionRegistry",
    "IntentNode",
    "IntentPlan",
    "GraphIRValidator",
    "LayoutDerivationEngine",
    "visualize",
    "GraphIRBuilder",
    "build_from_plan",
    "GraphIRPipeline",
    "run_pipeline",
]
