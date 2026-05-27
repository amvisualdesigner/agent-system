"""GraphIR Pipeline — orchestrates builder + layout + enrichment.

The pipeline is the HIGHEST-LEVEL GraphIR operation, converting
StructuralIR into a fully enriched (GraphIR + GraphIRLayout) pair
ready for backend rendering.

Pipeline order (immutable):
  1. Build: StructuralIR → GraphIR (via GraphIRBuilder.build_from_structural)
  2. Layout: GraphIR → GraphIRLayout (via LayoutDerivationEngine)
  3. Enrich: optional enrichment passes (future: import resolution,
     template binding, context injection)

The pipeline does NOT interact with renderers. It produces
(GraphIR, GraphIRLayout) and returns them. The caller (typically
apply_engine.py) passes both to a BackendRenderer.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.graphir.models import GraphIR, GraphIRLayout
from app.graphir.builder import GraphIRBuilder
from app.graphir.layout import LayoutDerivationEngine
from app.graphir.validator import GraphIRValidator

if TYPE_CHECKING:
    from app.engine.structural_completion import StructuralIR
    from app.graphir.structure.models import StructuralResolution


class GraphIRPipeline:
    """Immutable pipeline: StructuralIR → (GraphIR, GraphIRLayout).

    Stateless and deterministic. Each call runs the full pipeline.
    No caching, no global state, no side effects.

    Single entry point: run_from_structural(ir). 1:1 capability→node.
    """

    @staticmethod
    def run_from_structural(
        ir: StructuralIR,
        preferences: dict | None = None,
        resolution: StructuralResolution | None = None,
    ) -> tuple[GraphIR, GraphIRLayout]:
        """Run the full GraphIR pipeline from StructuralIR (new path).

        Structural IR bypasses binding redistribution entirely.
        1 capability → 1 node, no reinterpretation.

        Args:
            ir: StructuralIR with resolved capabilities.
            preferences: Optional layout preferences dict.
            resolution: Optional StructuralResolution del resolver.
                        Si se provee, pasa paths resueltos al builder.

        Returns:
            (GraphIR, GraphIRLayout) — frozen graph + derived layout.

        Raises:
            ValueError: on any pipeline failure.
        """
        graph = GraphIRBuilder.build_from_structural(
            ir,
            resolution=resolution,
        )
        GraphIRValidator.validate(graph)
        layout = LayoutDerivationEngine.derive(graph, preferences)
        return graph, layout


def run_from_structural(
    ir: StructuralIR,
    preferences: dict | None = None,
    resolution: StructuralResolution | None = None,
) -> tuple[GraphIR, GraphIRLayout]:
    """Convenience wrapper."""
    return GraphIRPipeline.run_from_structural(ir, preferences, resolution)
