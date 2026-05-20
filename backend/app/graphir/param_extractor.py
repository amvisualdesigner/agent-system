"""Param Extractor — extracts structured params from task for each intent.

Sits between decomposition and GraphIR construction:
  decompose_task() → ParamExtractor (enrich params) → GraphIR builder

Rules-first design: deterministic keyword/regex extraction for known patterns.
No LLM dependency. No parallel IR. Pure param enrichment.
"""

from __future__ import annotations

import re
import logging
from typing import Any

from app.graphir.intent import Intent, CapabilityDef

logger = logging.getLogger(__name__)


# ── Known business metrics (deterministic, no LLM) ─────────────────────

_KNOWN_METRICS: set[str] = {
    "revenue", "growth", "profit", "sales", "margin",
    "count", "sum", "average", "total", "ratio",
    "performance", "conversion", "retention",
    "churn", "cost", "spend", "budget", "forecast",
    "quantity", "volume", "price", "value",
}


# ── Regex patterns for structural extraction ───────────────────────────

_DIMENSION_RE = re.compile(
    r'\b(?:by|per|grouped by|broken down by)\s+'
    r'(\w+(?:\s+(?:and|,)\s+\w+)*)',
    re.IGNORECASE,
)

_TOP_K_RE = re.compile(r'\btop\s+(\d+)\b', re.IGNORECASE)

_TIME_GRANULARITY_RE = re.compile(
    r'\b(daily|weekly|monthly|quarterly|yearly)\b', re.IGNORECASE,
)

_COMMA_AND_RE = re.compile(r'\s*,\s*|\s+and\s+', re.IGNORECASE)


# ── Internal helpers ───────────────────────────────────────────────────


def _split_list(text: str) -> list[str]:
    """Split 'region and product' or 'region, product' into ['region', 'product']."""
    return [t.strip() for t in _COMMA_AND_RE.split(text) if t.strip()]


def _extract_metrics(task: str) -> list[str] | None:
    found = [m for m in _KNOWN_METRICS if m in task.lower()]
    return found if found else None


def _extract_dimensions(task: str) -> list[str] | None:
    match = _DIMENSION_RE.search(task)
    if not match:
        return None
    raw = match.group(1).strip()
    return _split_list(raw)


def _extract_top_k(task: str) -> int | None:
    match = _TOP_K_RE.search(task)
    if not match:
        return None
    return int(match.group(1))


def _extract_time_granularity(task: str) -> str | None:
    match = _TIME_GRANULARITY_RE.search(task)
    if not match:
        return None
    return match.group(1).lower()


def _extract_group_by(task: str) -> list[str] | None:
    return _extract_dimensions(task)


def _extract_columns(task: str) -> list[str] | None:
    dimensions = _extract_dimensions(task) or []
    metrics = _extract_metrics(task) or []
    seen: set[str] = set()
    combined: list[str] = []
    for item in dimensions + metrics:
        if item.lower() not in seen:
            combined.append(item)
            seen.add(item.lower())
    return combined if combined else None


# ── Capability-level param focus (intent-aware routing) ────────────────

_CAPABILITY_PARAM_FOCUS: dict[str, set[str]] = {
    "presentation.kpi_row": {"metrics"},
    "presentation.timeseries": {"metrics", "time_granularity", "group_by"},
    "presentation.table": {"columns", "metrics", "dimensions", "top_k"},
    "presentation.filter_panel": {"filters"},
    "presentation.chart.bar": {"metrics", "categories", "top_k"},
    "presentation.metric_card": {"metric"},
    "presentation.embed": {"src", "title"},
    "domain.analytics": {"metrics", "dimensions"},
    "domain.sales": {"metrics", "dimensions"},
    "data.export": {"format"},
    "data.drilldown": {"target"},
    "interaction.search": {"placeholder"},
    "interaction.form": {"fields"},
}


# ── Token normalization ────────────────────────────────────────────────


def _normalize_token_set(tokens: set[str]) -> set[str]:
    return {t.lower().strip() for t in tokens}


# ── Consumed token tracking (for structure layer ownership) ────────────

_EXTRACTOR_CONSUMED: dict[str, callable] = {
    "metrics": lambda v: _normalize_token_set(set(v)) if v else set(),
    "dimensions": lambda v: _normalize_token_set(set(v)) if v else set(),
    "columns": lambda v: _normalize_token_set(set(v)) if v else set(),
    "group_by": lambda v: _normalize_token_set(set(v)) if v else set(),
    "top_k": lambda v: {"top_rank_signal", "top"} if v is not None else set(),
    "time_granularity": lambda v: {v.lower().strip()} if v else set(),
}


# ── Extractor dispatch ─────────────────────────────────────────────────

_EXTRACTORS: dict[str, callable] = {
    "metrics": _extract_metrics,
    "dimensions": _extract_dimensions,
    "group_by": _extract_group_by,
    "columns": _extract_columns,
    "top_k": _extract_top_k,
    "time_granularity": _extract_time_granularity,
}


class ParamExtractor:
    """Extract structured params for each intent based on its param_schema.

    Intent-aware: each capability extracts only its relevant param fields
    (defined in _CAPABILITY_PARAM_FOCUS). Fallback to all schema fields
    for unknown capabilities.

    Tracks consumed_tokens for downstream structure layer ownership.

    Usage:
        extractor = ParamExtractor()
        extracted = extractor.extract(task, intent, contract)
        # extracted == {"metrics": ["revenue"], "dimensions": ["region"]}
    """

    def __init__(self):
        self.consumed_tokens: set[str] = set()

    def extract(self, task: str, intent: Intent, contract: CapabilityDef) -> dict[str, Any]:
        """Extract params for a single intent.

        Iterates the contract's param_schema fields and calls the
        registered extractor for each field. Filters by capability focus
        to avoid cross-intent param leakage. Updates consumed_tokens
        so the structure layer can respect token ownership.
        """
        if not contract.param_schema:
            return {}

        allowed = _CAPABILITY_PARAM_FOCUS.get(intent.capability, None)
        params: dict[str, Any] = {}
        for field, spec in contract.param_schema.items():
            if allowed is not None and field not in allowed:
                continue
            extractor = _EXTRACTORS.get(field)
            if extractor is None:
                continue
            try:
                value = extractor(task)
                if value is not None:
                    params[field] = value
                    consumed_fn = _EXTRACTOR_CONSUMED.get(field)
                    if consumed_fn:
                        self.consumed_tokens.update(consumed_fn(value))
            except Exception:
                logger.warning("ParamExtractor failed for field=%s cap=%s", field, intent.capability, exc_info=True)
        return params
