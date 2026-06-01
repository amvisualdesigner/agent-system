"""Semantic Frame — structured representation of user intent.

Keeps _OBJECT_KEYWORDS for 3B action binding and the StructuredSemanticFrame
dataclass for the confidence gate. Frame building is handled by IntentInterpreter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


_OBJECT_KEYWORDS: dict[str, str] = {
    "table": "table",
    "tabular": "table",
    "column": "table",
    "columns": "table",
    "data table": "table",
    "analytics table": "table",
    "kpi": "kpi_row",
    "metric": "kpi_row",
    "metrics": "kpi_row",
    "timeseries": "timeseries",
    "trend": "timeseries",
    "line": "timeseries",
    "line chart": "timeseries",
    "linechart": "timeseries",
    "chart": "chart",
    "bar chart": "chart.bar",
    "barchart": "chart.bar",
    "dashboard": "dashboard",
    "page": "page",
    "grid": "grid",
    "layout": "layout",
    "filter": "filter_panel",
    "facet": "filter_panel",
    "tfoot": "tfoot",
    "button": "button",
    "buttons": "button",
    "widget": "widget",
    "retention": "retention_widget",
    "container": "container",
    "panel": "container",
    "section": "container",
    "card": "card",
    "search": "search",
    "form": "form",
    "export": "export",
    "drilldown": "drilldown",
    "embed": "embed",
    "theme": "theme",
}


# ── Dataclasses ────────────────────────────────────────────────────


@dataclass
class ExtractedAction:
    verb: str
    direct_object: str
    confidence: float
    task_fragment: str = ""
    reference: str = ""
    position: str = ""


@dataclass
class ExtractedObject:
    type: str
    name: str | None = None
    existing_path: str | None = None
    confidence: float = 1.0


@dataclass
class ExtractedConstraint:
    param: str
    value: Any
    source: str = "inferred"
    confidence: float = 0.5


@dataclass
class StructuredSemanticFrame:
    actions: list[ExtractedAction] = field(default_factory=list)
    objects: list[ExtractedObject] = field(default_factory=list)
    constraints: list[ExtractedConstraint] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw_decomposition: Any = None
