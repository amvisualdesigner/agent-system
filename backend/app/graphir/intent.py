"""Intent — semantic intent model for the GraphIR pipeline.

Intent is the SINGLE source of truth for user intent through the entire pipeline:
  - Created during Intent Decomposition (Gate 1)
  - Consumed by Coverage Solve (Gate 2)
  - Referenced by GraphIRNode.metadata (Gate 3)
  - Revalidated in Coverage Revalidation (Gate 4)

IntentNode is preserved as a deprecated alias for backward compatibility
during the migration. Do NOT use IntentNode in new code.
"""

from __future__ import annotations

import hashlib

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Capability taxonomy ────────────────────────────────────────────
# Hierarchical prefixes: display.* / data.* / layout.* / interaction.*
# This prevents registry explosion while keeping matching precise.

CAPABILITY_DISPLAY_KPI     = "display.kpi_row"
CAPABILITY_DISPLAY_TS      = "display.timeseries"
CAPABILITY_DISPLAY_TABLE   = "display.analytics_table"
CAPABILITY_DISPLAY_FILTER  = "display.filter_panel"
CAPABILITY_EMBED           = "embed.external"
CAPABILITY_DATA_EXPORT     = "data.export"
CAPABILITY_DATA_DRILLDOWN  = "data.drilldown"
CAPABILITY_LAYOUT_PAGE     = "layout.page"
CAPABILITY_LAYOUT_CONTAINER = "layout.container"
CAPABILITY_LAYOUT_GRID     = "layout.grid"
CAPABILITY_INTERACTION_SEARCH = "interaction.search"
CAPABILITY_INTERACTION_FORM   = "interaction.form"


def make_intent_id(task_fragment: str, capability: str, seed: str = "") -> str:
    """Deterministic intent ID from task + capability + optional seed.

    Properties:
      - Same inputs → same ID (reproducible across runs)
      - Different task fragments → different IDs
      - No dependency on ordering or LLM non-determinism
    """
    raw = f"{task_fragment}::{capability}::{seed}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"intent_{h}"


@dataclass(frozen=True)
class Intent:
    """A single unit of user intent.

    Flows UNCHANGED through the entire pipeline. Every component
    references Intent.id — never duplicates identity.
    """
    id: str
    capability: str
    params: dict[str, Any] = field(default_factory=dict)
    task_fragment: str = ""
    weight: float = 1.0


# ── Legacy IntentNode (deprecated) ─────────────────────────────────

@dataclass(frozen=True)
class IntentNode:
    """DEPRECATED: Use Intent instead.

    A single semantic intent from natural language.
    'type' MUST be valid per IntentType + IntentExtensionRegistry.
    """
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    description: str | None = None


# ── IntentType enum (kept for backward compat) ─────────────────────

class IntentType(Enum):
    """Core semantic intent types — canonical and strict.
    """
    PAGE = "Page"
    KPIGROUP = "KPIGroup"
    CHART = "Chart"
    DATATABLE = "DataTable"
    FILTERPANEL = "FilterPanel"
    EMBED = "Embed"


_INTENT_TO_GRAPHIR_TYPE = {
    IntentType.PAGE: "Page",
    IntentType.KPIGROUP: "KpiRow",
    IntentType.CHART: "Timeseries",
    IntentType.DATATABLE: "AnalyticsTable",
    IntentType.FILTERPANEL: "FilterPanel",
    IntentType.EMBED: "Embed",
}

_INTENT_TO_EDGE_ROLE = {
    IntentType.KPIGROUP: "PRIMARY",
    IntentType.CHART: "SUPPORTING",
    IntentType.DATATABLE: "SUPPORTING",
    IntentType.FILTERPANEL: "SUPPORTING",
    IntentType.EMBED: "CONTAINS",
}

_CAPABILITY_TO_GRAPHIR_TYPE = {
    CAPABILITY_DISPLAY_KPI: "KpiRow",
    CAPABILITY_DISPLAY_TS: "Timeseries",
    CAPABILITY_DISPLAY_TABLE: "AnalyticsTable",
    CAPABILITY_DISPLAY_FILTER: "FilterPanel",
    CAPABILITY_EMBED: "Embed",
    CAPABILITY_LAYOUT_PAGE: "Page",
}

_CAPABILITY_TO_EDGE_ROLE = {
    CAPABILITY_DISPLAY_KPI: "PRIMARY",
    CAPABILITY_DISPLAY_TS: "SUPPORTING",
    CAPABILITY_DISPLAY_TABLE: "SUPPORTING",
    CAPABILITY_DISPLAY_FILTER: "SUPPORTING",
    CAPABILITY_EMBED: "CONTAINS",
}


class IntentExtensionRegistry:
    """Dynamic registry for semantic intent types (kept for compat)."""
    _extensions: dict[str, dict] = {}

    @classmethod
    def register(cls, name: str, config: dict) -> None:
        required = {"graphir_type", "edge_role"}
        missing = required - set(config.keys())
        if missing:
            raise ValueError(
                f"IntentExtension '{name}' missing required keys: {missing}"
            )
        if config["edge_role"] not in ("CONTAINS", "PRIMARY", "SUPPORTING"):
            raise ValueError(
                f"IntentExtension '{name}': edge_role must be one of "
                f"CONTAINS/PRIMARY/SUPPORTING, got '{config['edge_role']}'"
            )
        cls._extensions[name] = dict(config)

    @classmethod
    def unregister(cls, name: str) -> None:
        cls._extensions.pop(name, None)

    @classmethod
    def is_valid(cls, name: str) -> bool:
        return name in IntentType.__members__ or name in cls._extensions

    @classmethod
    def resolve_graphir_type(cls, name: str) -> str | None:
        if name in IntentType.__members__:
            return _INTENT_TO_GRAPHIR_TYPE.get(IntentType[name])
        ext = cls._extensions.get(name)
        return ext.get("graphir_type") if ext else None

    @classmethod
    def resolve_edge_role(cls, name: str) -> str | None:
        if name in IntentType.__members__:
            return _INTENT_TO_EDGE_ROLE.get(IntentType[name])
        ext = cls._extensions.get(name)
        return ext.get("edge_role") if ext else None

    @classmethod
    def list_extensions(cls) -> dict[str, dict]:
        return dict(cls._extensions)

    @classmethod
    def clear(cls) -> None:
        cls._extensions.clear()


# ── Capability resolvers (new — for Intent-based pipeline) ─────────

def resolve_graphir_type_from_capability(capability: str) -> str | None:
    """Map a capability string to the GraphIRNode.type it produces."""
    return _CAPABILITY_TO_GRAPHIR_TYPE.get(capability)


def resolve_edge_role_from_capability(capability: str) -> str | None:
    """Map a capability string to its default EdgeRole name."""
    return _CAPABILITY_TO_EDGE_ROLE.get(capability)


# ── IntentPlan ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class IntentPlan:
    """The bridge between decomposed intents and GraphIR.

    Intent is the single source of truth. IntentPlan holds the
    decomposed intents AND the contracts selected to satisfy them.

    Fields:
      intents:      the decomposed user intents (source of truth)
      contracts:    contracts selected to satisfy these intents
      params:       aggregated params from all contracts
      original_task: raw user input
      coverage_report: set after coverage solve (may be None)
    """
    intents: list[Intent]
    contracts: list[Any] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    original_task: str = ""
    coverage_report: Any = None

    @classmethod
    def validate(cls, plan: "IntentPlan") -> None:
        if not plan.intents:
            raise ValueError("IntentPlan must have at least one Intent")
        for i, intent in enumerate(plan.intents):
            if isinstance(intent, IntentNode):
                if not IntentExtensionRegistry.is_valid(intent.type):
                    raise ValueError(
                        f"IntentPlan.intents[{i}]: unknown intent type "
                        f"'{intent.type}'. Must be in IntentType or "
                        f"registered in IntentExtensionRegistry."
                    )
            elif isinstance(intent, Intent):
                if not resolve_graphir_type_from_capability(intent.capability):
                    raise ValueError(
                        f"IntentPlan.intents[{i}]: unknown capability "
                        f"'{intent.capability}'."
                    )
