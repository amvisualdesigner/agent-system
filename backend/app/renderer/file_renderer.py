"""File renderer: maps AST + renderer_config → FileOps.

Part of the Semantic UI IR Compiler — deterministic multi-phase
pipeline that converts a Semantic UI AST into executable FileOps.

Structural expansion only — no business logic, no branching, no decisions.
Templates use for loops and __VAR__ placeholders with JSON serialization.

Multiphase pipeline:
  build_component_tree → resolve_imports → resolve_slots → emit_tree

Phase 6 Propagation Invariant:
  compiler_config is required and must come from apply_engine.py.
  FileRenderer NEVER decides or creates CompilerConfig.
"""

import logging

from app.renderer.base import Renderer, FileOp
from app.renderer.component_node import build_component_tree, emit_tree, resolve_imports, resolve_slots
from app.renderer.compiler import CompilerConfig, validate_compiler_ir

logger = logging.getLogger(__name__)


class FileRenderer(Renderer):
    def render(
        self,
        ast: dict,
        renderer_config: dict,
        example_context: object | None = None,
        compiler_config: CompilerConfig | None = None,
    ) -> list[FileOp]:
        if compiler_config is None:
            raise RuntimeError(
                "FileRenderer.render() requires explicit compiler_config. "
                "Pipeline mode must be set by the caller."
            )

        root = build_component_tree(ast, renderer_config, example_context)
        resolve_imports(root)
        resolve_slots(root)

        if compiler_config.mode != "legacy":
            validate_compiler_ir(root, compiler_config)

        return emit_tree(root)
