"""Semantic Frame — structured representation of user intent.

Transforms raw task + DecompositionResult into a StructuredSemanticFrame
with explicit actions, objects, constraints, and confidence.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Any

from app.graphir.intent import CAPABILITY_REGISTRY

logger = logging.getLogger(__name__)


# ── Action/object/constraint patterns ──────────────────────────────

_ACTION_PATTERNS: list[tuple[list[str], str, float]] = [
    (["add", "adding", "include", "insert"], "add", 0.9),
    (["modify", "edit", "update", "change"], "modify", 0.9),
    (["create", "build", "generate", "compose", "design"], "create", 0.9),
    (["override", "overwrite", "replace", "use instead"], "override", 0.9),
    (["remove", "delete", "destroy"], "remove", 0.9),
    (["show", "display", "demonstrate"], "show", 0.8),
    (["apply", "set", "configure", "enable"], "apply", 0.8),
]

_OBJECT_KEYWORDS: dict[str, str] = {
    "table": "table",
    "tabular": "table",
    "data table": "table",
    "analytics table": "table",
    "kpi": "kpi_row",
    "metric": "kpi_row",
    "metrics": "kpi_row",
    "timeseries": "timeseries",
    "trend": "timeseries",
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
    source: str = "inferred"  # "explicit" | "inferred"
    confidence: float = 0.5


@dataclass
class StructuredSemanticFrame:
    actions: list[ExtractedAction] = field(default_factory=list)
    objects: list[ExtractedObject] = field(default_factory=list)
    constraints: list[ExtractedConstraint] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw_decomposition: Any = None


# ── Frame builder ──────────────────────────────────────────────────


def _extract_actions(task_lower: str) -> list[ExtractedAction]:
    """Extract action verbs and their objects from task text."""
    actions: list[ExtractedAction] = []
    seen: set[str] = set()

    for keywords, verb, conf in _ACTION_PATTERNS:
        for kw in keywords:
            if kw in task_lower:
                key = f"{verb}:{kw}"
                if key in seen:
                    continue
                seen.add(key)

                # Try to extract the direct object after the verb
                # e.g., "add tfoot" -> object="tfoot"
                obj = ""
                for m in re.finditer(rf"\b{re.escape(kw)}\s+(\w+)", task_lower):
                    obj = m.group(1)
                    break

                actions.append(ExtractedAction(
                    verb=verb,
                    direct_object=obj,
                    confidence=conf,
                    task_fragment=kw if not obj else f"{kw} {obj}",
                ))
                break  # one match per verb type

    return actions


def _extract_objects(task_lower: str, intents: list) -> list[ExtractedObject]:
    """Extract objects from task text and match to known capability types."""
    objects: list[ExtractedObject] = []
    seen: set[str] = set()

    # First, collect types already detected by decomposition
    detected_types = {i.capability.split(".")[-1] for i in intents}
    for dtype in detected_types:
        objects.append(ExtractedObject(type=dtype, confidence=0.9))

    # Then scan task for additional object keywords
    for keyword, obj_type in _OBJECT_KEYWORDS.items():
        if keyword in task_lower:
            if obj_type in seen:
                continue
            if any(o.type == obj_type for o in objects):
                continue
            seen.add(obj_type)
            objects.append(ExtractedObject(type=obj_type, confidence=0.8))

    return objects


def _extract_constraints(task: str, task_lower: str) -> list[ExtractedConstraint]:
    """Extract explicit constraints from task text.

    Constraints are specific parameters the user explicitly sets:
    - "use net_revenue instead of revenue" → metrics=["net_revenue"]
    - "monthly time configuration" → time_granularity="monthly"
    - "top 5" → top_k=5
    """
    constraints: list[ExtractedConstraint] = []

    # Override pattern: "use X instead of Y" or "X instead of Y"
    override_m = re.search(r'use\s+(\w+)\s+instead\s+of\s+(\w+)', task_lower)
    if override_m:
        constraints.append(ExtractedConstraint(
            param="metrics",
            value=[override_m.group(1)],
            source="explicit",
            confidence=0.95,
        ))

    # Override variant: "override ... so that ... uses X"
    override_m2 = re.search(r'override.*?uses?\s+(?:only\s+)?(\w+)', task_lower)
    if override_m2 and not any(c.param == "metrics" for c in constraints):
        constraints.append(ExtractedConstraint(
            param="metrics",
            value=[override_m2.group(1)],
            source="explicit",
            confidence=0.9,
        ))

    # Time granularity: "monthly", "weekly", "daily"
    time_m = re.search(r'\b(monthly|weekly|daily)\s+(time\s+)?(config|granularity|aggregation)?', task_lower)
    if time_m:
        constraints.append(ExtractedConstraint(
            param="time_granularity",
            value=time_m.group(1),
            source="explicit",
            confidence=0.85,
        ))

    # Top-K: "top 5", "top 10"
    top_m = re.search(r'\btop\s+(\d+)\b', task_lower)
    if top_m:
        constraints.append(ExtractedConstraint(
            param="top_k",
            value=int(top_m.group(1)),
            source="explicit",
            confidence=0.9,
        ))

    # Known metrics in task
    known_metrics = {"revenue", "growth", "profit", "sales", "margin",
                     "retention", "churn", "cost", "conversion",
                     "net_revenue"}
    found_metrics = [m for m in known_metrics if m in task_lower]
    if found_metrics:
        constraints.append(ExtractedConstraint(
            param="mentioned_metrics",
            value=found_metrics,
            source="explicit",
            confidence=0.7,
        ))

    return constraints


def _compute_frame_confidence(
    actions: list[ExtractedAction],
    objects: list[ExtractedObject],
    constraints: list[ExtractedConstraint],
    missing_info: list[str],
) -> float:
    """Compute frame confidence from action/object/constraint coverage.

    Weighting:
    - Actions: 40% (most important — what to do)
    - Objects: 35% (what to do it to)
    - Constraints: 25% (how to do it)
    - Missing info penalty: -5% per unresolved fragment
    """
    action_conf = (
        sum(a.confidence for a in actions) / len(actions) * 0.4
        if actions else 0.0
    )

    # Object confidence: at least one object with good confidence
    if objects:
        best_obj = max(o.confidence for o in objects)
        object_conf = best_obj * 0.35
    else:
        object_conf = 0.0

    constraint_conf = (
        sum(c.confidence for c in constraints if c.source == "explicit") / max(len(constraints), 1) * 0.25
        if constraints else 0.0
    )

    missing_penalty = max(0.0, 1.0 - len(missing_info) * 0.05)

    raw = (action_conf + object_conf + constraint_conf) * missing_penalty
    return max(0.0, min(1.0, raw))


def build_frame_from_decomposition(
    task: str,
    decomposition_result: Any,
) -> StructuredSemanticFrame:
    """Build a StructuredSemanticFrame from raw task + DecompositionResult.

    Uses the existing decomposition intents as a base, then enhances
    with explicit action/object/constraint extraction.
    """
    task_lower = task.lower()

    # 1. Extract actions
    actions = _extract_actions(task_lower)

    # 2. Extract objects
    objects = _extract_objects(task_lower, decomposition_result.intents)

    # 3. Extract constraints
    constraints = _extract_constraints(task, task_lower)

    # 4. Compute frame's own missing info: tokens we couldn't classify
    # as actions, objects, or constraints (not reusing decomposition.unresolved)
    task_tokens = set(re.findall(r'\w+', task_lower))
    stopwords = {"the", "a", "an", "and", "or", "of", "to", "in",
                 "for", "with", "by", "on", "at", "is", "be", "this",
                 "that", "it", "as", "but", "not", "or", "from", "are",
                 "was", "were", "been", "being", "have", "has", "had",
                 "do", "does", "did", "will", "would", "could", "should",
                 "may", "might", "shall", "can", "need", "da", "que",
                 "en", "el", "la", "los", "las", "un", "una", "con",
                 "para", "por", "al", "del", "lo", "se", "no", "es",
                 "como", "mas", "pero", "sus", "le", "ya", "este",
                 "entre", "porque", "todo", "esta", "sin", "ella",
                 "ello", "cada", "otro", "ser", "haber", "tener",
                 "hacer", "estar", "very", "just", "only", "also",
                 "so", "if", "then", "than", "that", "this", "these",
                 "those", "i", "me", "my", "myself", "we", "our",
                 "ours", "you", "your", "yours", "he", "him", "his",
                 "she", "her", "hers", "it", "its", "they", "them",
                 "their", "theirs", "what", "which", "who", "whom",
                 "when", "where", "why", "how", "all", "each",
                 "some", "any", "both", "few", "more", "most",
                 "other", "such", "not", "nor", "too", "called",
                 "instead", "using", "must", "include", "additionally",
                 "system", "test", "consistency", "verify", "uses",
                 "depends", "handling", "simple", "reporting",
                 "standard", "secondary", "third", "node", "type",
                 "configuration", "default", "registered",
                 "additionally", "audit", "render", "missing"}
    
    # Tokens already classified by our extraction
    classified = set()
    for a in actions:
        classified.update(a.task_fragment.lower().split())
        classified.add(a.verb)
        if a.direct_object:
            classified.add(a.direct_object)
    for o in objects:
        classified.add(o.type)
    for c in constraints:
        if isinstance(c.value, str):
            classified.add(c.value.lower())
        elif isinstance(c.value, list):
            classified.update(str(v).lower() for v in c.value)
    
    meaningful = task_tokens - stopwords - classified
    missing = sorted(meaningful)[:10]  # cap at 10 to keep penalty bounded

    # 5. Compute confidence
    confidence = _compute_frame_confidence(actions, objects, constraints, missing)

    return StructuredSemanticFrame(
        actions=actions,
        objects=objects,
        constraints=constraints,
        missing_info=missing,
        confidence=confidence,
        raw_decomposition=decomposition_result,
    )
