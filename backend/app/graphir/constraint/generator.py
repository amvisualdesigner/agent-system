"""ContentGenerator — generates content for a single GraphIR node.

Pure Core: zero IO, no file awareness.

Extracted from RepositoryAwareRenderer to separate content generation
from structural merge logic. Each generator produces content for
exactly one graph node type.

Generators declare their ExtendStrategy to control how EXTEND
decisions are applied (Phase 5).
"""

from __future__ import annotations

from app.graphir.backends import ReactBackend, BackendConfig
from app.graphir.constraint.diff import ExtendStrategy


class ContentGenerator:
    """Generates content for GraphIR nodes.

    Wraps ReactBackend generators and provides strategy metadata
    for the structural diff engine.

    Phase 5a: extracted from renderer, no behavioral change.
    Phase 5b+: generators opt into APPEND_REGION.
    """

    def __init__(self):
        self.react_backend = ReactBackend()
        # node_type → ExtendStrategy
        # Default REPLACE_FILE preserves Phase 1 behavior.
        self._extend_strategies: dict[str, ExtendStrategy] = {}

    def generate(
        self,
        node,
        layout,
        config: BackendConfig,
    ) -> str:
        """Generate content for a single graph node.

        Delegates to ReactBackend._generators[node.type].

        Args:
            node: GraphIR node
            layout: GraphIRLayout
            config: BackendConfig

        Returns:
            Generated content as string.

        Raises:
            KeyError if node.type has no registered generator.
        """
        generator = self.react_backend._generators.get(node.type)
        if generator is None:
            raise KeyError(f"No generator registered for node type: {node.type}")

        node_constraints = layout.constraints.get(node.id, [])
        return generator(node, node_constraints, self.react_backend, config)

    def get_extend_strategy(self, node_type: str) -> ExtendStrategy:
        """Return the extend strategy for a node type.

        Default: REPLACE_FILE (backward compatible).
        """
        return self._extend_strategies.get(node_type, ExtendStrategy.REPLACE_FILE)

    def set_extend_strategy(
        self, node_type: str, strategy: ExtendStrategy,
    ) -> None:
        """Register an extend strategy for a node type."""
        self._extend_strategies[node_type] = strategy
