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
    Intent,
    IntentType,
    IntentExtensionRegistry,
    IntentNode,
    IntentPlan,
    make_intent_id,
    resolve_graphir_type_from_capability,
    resolve_edge_role_from_capability,
)

from app.graphir.intent_decomposition import decompose_task

from app.graphir.intent_coverage import (
    CapabilityMatch,
    MissingIntent,
    CoverageReport,
    IntentCoverageValidator,
    IntentCoverageError,
)

from app.graphir.validator import GraphIRValidator

from app.graphir.layout import LayoutDerivationEngine

from app.graphir.intent_embedding import (
    EmbeddingIndex,
    cosine_similarity,
    compute_embedding,
    rank_capabilities,
    get_embedding_index,
    reset_embedding_index,
)

from app.graphir.intent_governance import (
    OrphanReport,
    UnresolvedReport,
    UnresolvedTracker,
    CoOccurrenceReport,
    CoOccurrenceTracker,
    ContractCoverageReport,
    ContractCoverageGapDetector,
    EmbeddingFPTracker,
    FPReport,
    detect_orphans,
)

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
    "IntentType",
    "IntentExtensionRegistry",
    "IntentNode",
    "IntentPlan",
    "make_intent_id",
    "resolve_graphir_type_from_capability",
    "resolve_edge_role_from_capability",
    "decompose_task",
    "CapabilityMatch",
    "MissingIntent",
    "CoverageReport",
    "IntentCoverageValidator",
    "IntentCoverageError",
    "GraphIRValidator",
    "LayoutDerivationEngine",
    "visualize",
    "GraphIRBuilder",
    "build_from_structural",
    "GraphIRPipeline",
    "run_from_structural",
    "EmbeddingIndex",
    "cosine_similarity",
    "compute_embedding",
    "rank_capabilities",
    "get_embedding_index",
    "reset_embedding_index",
    "OrphanReport",
    "UnresolvedReport",
    "UnresolvedTracker",
    "CoOccurrenceReport",
    "CoOccurrenceTracker",
    "ContractCoverageReport",
    "ContractCoverageGapDetector",
    "EmbeddingFPTracker",
    "FPReport",
    "detect_orphans",
]
