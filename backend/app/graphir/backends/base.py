"""BackendRenderer ABC — stateless, dumb translator: GraphIR → list[FileOp].

The renderer receives a COMPLETE GraphIR + COMPLETE GraphIRLayout
and produces output files. It is the terminal stage of the pipeline.

The renderer does NOT:
  - Decide what components exist (that is GraphIR's job)
  - Decide how components compose (that is edges' job)
  - Derive layout (that is LayoutDerivationEngine's job)
  - Modify or enrich the graph
  - Cache internal state between calls
  - Interpret graph topology
  - Read IntentPlan or contracts
  - Make layout decisions from EdgeRole directly

The renderer ONLY:
  - Maps GraphIRNode.type → framework component implementation
  - Converts GraphIRNode.data → framework props
  - Converts LayoutConstraint → framework styling
  - Generates imports as a syntactic byproduct
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.graphir.models import GraphIR, GraphIRLayout
from app.graphir.models import FileOp


@dataclass(frozen=True)
class BackendConfig:
    """Configuration for a specific renderer backend.

    ONLY syntactic conventions. NO structural information.

    path_map: optional mapping of GraphIRNode.type → relative file path.
              If absent, paths are derived as {output_base_path}/{type}{extension}.
    file_path_overrides: explicit full paths (relative to workspace) that override
                         path_map for specific node types. Used when the real repo
                         file layout differs from the contract templates
                         (e.g., SalesOverviewPage.tsx instead of Page.tsx).
    """
    framework: str = "react"
    file_extension: str = ".tsx"
    component_style: str = "PascalCase"
    output_base_path: str = "src/"
    path_map: dict[str, str] = field(default_factory=dict)
    file_path_overrides: dict[str, str] = field(default_factory=dict)
    component_signatures: dict[str, dict] = field(default_factory=dict)
    workspace: str | None = None


class BackendRenderer(ABC):
    """Stateless, dumb translator: GraphIR → list[FileOp].

    Subclasses implement framework-specific rendering logic.
    All subclasses MUST be stateless (no caching between calls).
    """

    @abstractmethod
    def render(
        self,
        graph: GraphIR,
        layout: GraphIRLayout,
        config: BackendConfig,
    ) -> list[FileOp]:
        ...

    @abstractmethod
    def framework_name(self) -> str:
        ...
