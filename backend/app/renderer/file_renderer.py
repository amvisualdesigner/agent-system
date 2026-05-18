"""File renderer: maps AST + renderer_config → FileOps.

Structural expansion only — no business logic, no branching, no decisions.
Templates use for loops and __VAR__ placeholders with JSON serialization.

Internally builds a ComponentNode tree for ownership-scoped rendering.
External API unchanged for backward compatibility.
"""

import logging

from app.renderer.base import Renderer, FileOp
from app.renderer.component_node import build_component_tree, emit_tree, resolve_imports, resolve_slots

logger = logging.getLogger(__name__)


class FileRenderer(Renderer):
    def render(
        self,
        ast: dict,
        renderer_config: dict,
        example_context: object | None = None,
    ) -> list[FileOp]:
        # Build component tree from flat AST + config
        # The tree enforces ownership boundaries: each node renders
        # with ONLY its own props — no global context mutation.
        root = build_component_tree(ast, renderer_config, example_context)
        resolve_imports(root)
        resolve_slots(root)
        return emit_tree(root)
