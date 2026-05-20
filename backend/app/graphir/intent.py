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
import re

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# Phase 1: Capability Ontology (new taxonomy)
# ═══════════════════════════════════════════════════════════════════

class CapabilityCategory(Enum):
    SEMANTIC = "semantic"
    STRUCTURAL = "structural"
    STYLISTIC = "stylistic"


@dataclass(frozen=True)
class CapabilityAxis:
    presentation: str | None = None
    domain: str | None = None
    layout: str | None = None
    style: str | None = None


@dataclass(frozen=True)
class CapabilityDef:
    id: str
    axes: CapabilityAxis
    category: CapabilityCategory
    parent: str | None = None
    description: str = ""
    is_soft: bool = False


CAPABILITY_REGISTRY: dict[str, CapabilityDef] = {
    # ── Presentation (domain-agnostic visual components) ──
    "presentation.kpi_row": CapabilityDef(
        id="presentation.kpi_row",
        axes=CapabilityAxis(presentation="kpi_row"),
        category=CapabilityCategory.SEMANTIC,
        description="Row of KPI metric cards showing key numbers",
    ),
    "presentation.timeseries": CapabilityDef(
        id="presentation.timeseries",
        axes=CapabilityAxis(presentation="timeseries"),
        category=CapabilityCategory.SEMANTIC,
        description="Time series chart showing trends over time",
    ),
    "presentation.table": CapabilityDef(
        id="presentation.table",
        axes=CapabilityAxis(presentation="table"),
        category=CapabilityCategory.SEMANTIC,
        description="Data table with columns and rows (domain-agnostic)",
    ),
    "presentation.filter_panel": CapabilityDef(
        id="presentation.filter_panel",
        axes=CapabilityAxis(presentation="filter_panel"),
        category=CapabilityCategory.SEMANTIC,
        description="Filter panel for refining data displayed",
    ),
    "presentation.chart.bar": CapabilityDef(
        id="presentation.chart.bar",
        axes=CapabilityAxis(presentation="chart.bar"),
        category=CapabilityCategory.SEMANTIC,
        description="Bar chart for categorical comparison",
    ),
    "presentation.metric_card": CapabilityDef(
        id="presentation.metric_card",
        axes=CapabilityAxis(presentation="metric_card"),
        category=CapabilityCategory.SEMANTIC,
        description="Single metric card with value and label",
    ),
    "presentation.embed": CapabilityDef(
        id="presentation.embed",
        axes=CapabilityAxis(presentation="embed"),
        category=CapabilityCategory.SEMANTIC,
        description="Embedded external content (iframe/widget)",
    ),
    # ── Domain (semantic context, enriches presentation) ──
    "domain.analytics": CapabilityDef(
        id="domain.analytics",
        axes=CapabilityAxis(domain="analytics"),
        category=CapabilityCategory.SEMANTIC,
        description="Analytics domain context — KPIs, metrics, business data",
    ),
    "domain.sales": CapabilityDef(
        id="domain.sales",
        axes=CapabilityAxis(domain="sales"),
        category=CapabilityCategory.SEMANTIC,
        description="Sales domain context — revenue, growth, deals",
    ),
    # ── Data (actions on data) ──
    "data.export": CapabilityDef(
        id="data.export",
        axes=CapabilityAxis(presentation="export"),
        category=CapabilityCategory.SEMANTIC,
        description="Export data to external format (CSV, PDF)",
    ),
    "data.drilldown": CapabilityDef(
        id="data.drilldown",
        axes=CapabilityAxis(domain="drilldown"),
        category=CapabilityCategory.SEMANTIC,
        description="Drill down into data details",
    ),
    # ── Interaction ──
    "interaction.search": CapabilityDef(
        id="interaction.search",
        axes=CapabilityAxis(presentation="search"),
        category=CapabilityCategory.SEMANTIC,
        description="Search/lookup functionality",
    ),
    "interaction.form": CapabilityDef(
        id="interaction.form",
        axes=CapabilityAxis(presentation="form"),
        category=CapabilityCategory.SEMANTIC,
        description="Form with input fields and submission",
    ),
    # ── Layout (structural — soft) ──
    "layout.page": CapabilityDef(
        id="layout.page",
        axes=CapabilityAxis(layout="page"),
        category=CapabilityCategory.STRUCTURAL,
        description="Page-level layout container",
        is_soft=True,
    ),
    "layout.grid": CapabilityDef(
        id="layout.grid",
        axes=CapabilityAxis(layout="grid"),
        category=CapabilityCategory.STRUCTURAL,
        description="Grid layout for arranging children",
        is_soft=True,
    ),
    "layout.container": CapabilityDef(
        id="layout.container",
        axes=CapabilityAxis(layout="container"),
        category=CapabilityCategory.STRUCTURAL,
        description="Generic container for grouping content",
        is_soft=True,
    ),
    # ── Style (stylistic — soft) ──
    "style.theme.light": CapabilityDef(
        id="style.theme.light",
        axes=CapabilityAxis(style="theme.light"),
        category=CapabilityCategory.STYLISTIC,
        description="Light color theme",
        is_soft=True,
    ),
    "style.theme.dark": CapabilityDef(
        id="style.theme.dark",
        axes=CapabilityAxis(style="theme.dark"),
        category=CapabilityCategory.STYLISTIC,
        description="Dark color theme",
        is_soft=True,
    ),
    "style.theme.enterprise": CapabilityDef(
        id="style.theme.enterprise",
        axes=CapabilityAxis(style="theme.enterprise"),
        category=CapabilityCategory.STYLISTIC,
        description="Enterprise brand theme",
        is_soft=True,
    ),
    "style.card.elevated": CapabilityDef(
        id="style.card.elevated",
        axes=CapabilityAxis(style="card.elevated"),
        category=CapabilityCategory.STYLISTIC,
        description="Elevated card with shadow depth",
        is_soft=True,
    ),
}


def resolve_capability_def(capability: str) -> CapabilityDef | None:
    if capability in CAPABILITY_REGISTRY:
        return CAPABILITY_REGISTRY[capability]
    alias = _CAPABILITY_ALIASES.get(capability)
    if alias and alias in CAPABILITY_REGISTRY:
        return CAPABILITY_REGISTRY[alias]
    return None


def is_capability_soft(capability: str) -> bool:
    cap = resolve_capability_def(capability)
    return cap.is_soft if cap else False


# ── Backward-compatible capability aliases (deprecated) ──────────
# Old capability strings still resolve correctly.
# Use new IDs in new code.

_CAPABILITY_ALIASES: dict[str, str] = {
    "display.kpi_row": "presentation.kpi_row",
    "display.timeseries": "presentation.timeseries",
    "display.analytics_table": "presentation.table",
    "display.filter_panel": "presentation.filter_panel",
    "embed.external": "presentation.embed",
    "layout.page": "layout.page",
    "layout.container": "layout.container",
    "layout.grid": "layout.grid",
    "interaction.search": "interaction.search",
    "interaction.form": "interaction.form",
    "data.export": "data.export",
    "data.drilldown": "data.drilldown",
}

# ── Deprecated capability constants (kept for backward compat) ───
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


# ── Semantic entropy ─────────────────────────────────────────────


def compute_semantic_entropy(task: str, matched_intents: list[Intent] | None = None) -> float:
    """Compute semantic entropy: measures decomposition ambiguity.

    Range: 0.0 (very specific) to 1.0 (very ambiguous).

    Based on token coverage: what fraction of the task's tokens were
    matched by intent keywords.

    "haz algo bonito" → high entropy (few matched tokens)
    "dashboard ventas con tabla kpi" → low entropy (most tokens matched)
    """
    if not task or not task.strip():
        return 1.0

    task_lower = task.lower()
    tokens = set(re.findall(r'\w+', task_lower))
    if not tokens:
        return 1.0

    matched_tokens: set[str] = set()
    if matched_intents:
        for intent in matched_intents:
            fragment = intent.task_fragment.lower() if intent.task_fragment else ""
            if fragment:
                matched_tokens.update(re.findall(r'\w+', fragment))

    if not matched_tokens:
        return 1.0

    overlap = len(tokens & matched_tokens)
    ratio = overlap / len(tokens) if tokens else 0.0
    return max(0.0, min(1.0, 1.0 - ratio))


# ── Intent ID generation ─────────────────────────────────────────


def make_intent_id(task_fragment: str, capability: str, seed: str = "") -> str:
    raw = f"{task_fragment}::{capability}::{seed}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"intent_{h}"


# ── Intent dataclass ─────────────────────────────────────────────


@dataclass(frozen=True)
class Intent:
    id: str
    capability: str
    params: dict[str, Any] = field(default_factory=dict)
    task_fragment: str = ""
    weight: float = 1.0
    source: str = "keyword"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "capability": self.capability,
            "params": self.params,
            "task_fragment": self.task_fragment,
            "weight": self.weight,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Intent:
        return cls(
            id=d["id"],
            capability=d["capability"],
            params=dict(d.get("params", {})),
            task_fragment=d.get("task_fragment", ""),
            weight=d.get("weight", 1.0),
            source=d.get("source", "keyword"),
        )


# ── Legacy IntentNode (deprecated) ───────────────────────────────


@dataclass(frozen=True)
class IntentNode:
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    description: str | None = None


# ── IntentType enum (kept for backward compat) ───────────────────


class IntentType(Enum):
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

# Capability → GraphIRNode.type mapping (includes both new and old IDs)
_CAPABILITY_TO_GRAPHIR_TYPE: dict[str, str] = {
    # New canonical IDs
    "presentation.kpi_row": "KpiRow",
    "presentation.timeseries": "Timeseries",
    "presentation.table": "AnalyticsTable",
    "presentation.filter_panel": "FilterPanel",
    "presentation.embed": "Embed",
    "presentation.chart.bar": "BarChart",
    "presentation.metric_card": "MetricCard",
    "data.export": "ExportButton",
    "data.drilldown": "Drilldown",
    "interaction.search": "SearchBar",
    "interaction.form": "Form",
    "layout.page": "Page",
    # Deprecated aliases (backward compat)
    "display.kpi_row": "KpiRow",
    "display.timeseries": "Timeseries",
    "display.analytics_table": "AnalyticsTable",
    "display.filter_panel": "FilterPanel",
    "embed.external": "Embed",
}

_CAPABILITY_TO_EDGE_ROLE: dict[str, str] = {
    # New canonical IDs
    "presentation.kpi_row": "PRIMARY",
    "presentation.timeseries": "SUPPORTING",
    "presentation.table": "SUPPORTING",
    "presentation.filter_panel": "SUPPORTING",
    "presentation.embed": "CONTAINS",
    "presentation.chart.bar": "SUPPORTING",
    "presentation.metric_card": "PRIMARY",
    "data.export": "SUPPORTING",
    "data.drilldown": "SUPPORTING",
    "interaction.search": "SUPPORTING",
    "interaction.form": "SUPPORTING",
    # Deprecated aliases (backward compat)
    "display.kpi_row": "PRIMARY",
    "display.timeseries": "SUPPORTING",
    "display.analytics_table": "SUPPORTING",
    "display.filter_panel": "SUPPORTING",
    "embed.external": "CONTAINS",
}


# ── IntentExtensionRegistry (deprecated, kept for compat) ────────


class IntentExtensionRegistry:
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


# ── Node vs metadata distinction ─────────────────────────────────
# Some capabilities create GraphIR nodes (presentation.*, data.*, interaction.*).
# Others (domain.*, layout.grid, layout.container, style.*) are metadata-only
# — they enrich graph params or sibling node metadata but never become nodes.

_GRAPHIR_NODE_CAPABILITIES: set[str] = {
    "presentation.kpi_row",
    "presentation.timeseries",
    "presentation.table",
    "presentation.filter_panel",
    "presentation.chart.bar",
    "presentation.metric_card",
    "presentation.embed",
    "data.export",
    "data.drilldown",
    "interaction.search",
    "interaction.form",
    "layout.page",
}


def is_graphir_node_capability(capability: str) -> bool:
    """Whether a capability creates a GraphIR node (vs metadata-only)."""
    return capability in _GRAPHIR_NODE_CAPABILITIES


def is_capability_metadata(capability: str) -> bool:
    """Whether a capability is metadata-only (no GraphIR node created)."""
    cap = resolve_capability_def(capability)
    if cap is None:
        return False
    return cap.id not in _GRAPHIR_NODE_CAPABILITIES


# ── Capability resolvers ─────────────────────────────────────────


def resolve_graphir_type_from_capability(capability: str) -> str | None:
    return _CAPABILITY_TO_GRAPHIR_TYPE.get(capability)


def resolve_edge_role_from_capability(capability: str) -> str | None:
    return _CAPABILITY_TO_EDGE_ROLE.get(capability)


# ── IntentPlan ───────────────────────────────────────────────────


@dataclass(frozen=True)
class IntentPlan:
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
                cap = intent.capability
                # Truly unknown capabilities (not in registry at all) should fail
                if not resolve_capability_def(cap):
                    raise ValueError(
                        f"IntentPlan.intents[{i}]: unknown capability "
                        f"'{cap}'."
                    )
                # Node capabilities must have a graphir type mapping
                if is_graphir_node_capability(cap):
                    if not resolve_graphir_type_from_capability(cap):
                        raise ValueError(
                            f"IntentPlan.intents[{i}]: capability '{cap}' "
                            f"has no graphir_type mapping."
                        )
                # Metadata-only capabilities (domain, style, layout.grid, layout.container)
                # are allowed without GraphIR type — they enrich graph params.
