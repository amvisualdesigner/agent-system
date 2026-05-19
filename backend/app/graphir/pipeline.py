"""GraphIR Pipeline — orchestrates builder + layout + enrichment.

The pipeline is the HIGHEST-LEVEL GraphIR operation, converting
an IntentPlan into a fully enriched (GraphIR + GraphIRLayout) pair
ready for backend rendering.

Pipeline order (immutable):
  1. Build: IntentPlan → GraphIR (via GraphIRBuilder)
  2. Layout: GraphIR → GraphIRLayout (via LayoutDerivationEngine)
  3. Enrich: optional enrichment passes (future: import resolution,
     template binding, context injection)

The pipeline does NOT interact with renderers. It produces
(GraphIR, GraphIRLayout) and returns them. The caller (typically
apply_engine.py) passes both to a BackendRenderer.
"""
from __future__ import annotations

from typing import Any

from app.graphir.intent import IntentPlan
from app.graphir.models import GraphIR, GraphIRLayout
from app.graphir.builder import GraphIRBuilder
from app.graphir.layout import LayoutDerivationEngine
from app.graphir.validator import GraphIRValidator


class GraphIRPipeline:
    """Immutable pipeline: IntentPlan → (GraphIR, GraphIRLayout).

    Stateless and deterministic. Each call runs the full pipeline.
    No caching, no global state, no side effects.
    """

    @staticmethod
    def run(
        plan: IntentPlan,
        preferences: dict | None = None,
    ) -> tuple[GraphIR, GraphIRLayout]:
        """Run the full GraphIR pipeline.

        Args:
            plan: Validated IntentPlan.
            preferences: Optional layout preferences dict.

        Returns:
            (GraphIR, GraphIRLayout) — frozen graph + derived layout.

        Raises:
            ValueError: on any pipeline failure.
        """
        graph = GraphIRBuilder.build(plan)
        GraphIRValidator.validate(graph)
        layout = LayoutDerivationEngine.derive(graph, preferences)
        return graph, layout


def run_pipeline(
    plan: IntentPlan,
    preferences: dict | None = None,
) -> tuple[GraphIR, GraphIRLayout]:
    """Convenience wrapper."""
    return GraphIRPipeline.run(plan, preferences)
