"""INTERNAL CONTRACT — single source of truth for file path resolution.

Unifies Path A (ReactBackend._resolve_file_path) and Path B
(decision.target_file) into one function. Path A calls resolve()
directly. Path B uses decision.target_file with resolve() as fallback.

Contract:
    - resolve() is the ONLY file path resolution function.
    - No renderer implements its own path resolution logic.
    - Input: UIGeneratorContext (or any object with .type) + BackendConfig.
    - Output: normalized relative path within the output base.
    - type→path_map override takes precedence; otherwise type+extension.
"""

from __future__ import annotations

import os

from app.graphir.backends.base import BackendConfig
from app.graphir.ui_ir import UIGeneratorContext


class FilePathResolver:
    """Path resolution — file_path_overrides → path_map → derive with single fallback."""

    @staticmethod
    def resolve(node: UIGeneratorContext, config: BackendConfig) -> str:
        # 1. Explicit file path overrides (real repo file layout)
        if config.file_path_overrides and node.type in config.file_path_overrides:
            return os.path.normpath(config.file_path_overrides[node.type])
        # 2. path_map + output_base_path (contract template conventions)
        if node.type in config.path_map:
            override = config.path_map[node.type]
            base = config.output_base_path.rstrip("/")
            return os.path.normpath(f"{base}/{override}")
        # 3. Fallback: output_base_path + type + extension
        base = config.output_base_path.rstrip("/")
        return os.path.normpath(f"{base}/{node.type}{config.file_extension}")
