"""Intent Structure Annotator — post-hoc structural enrichment for intents.

Takes already-decomposed intents + unresolved fragments and annotates
each intent with weak UI priors:

  - layout_role: primary / secondary (by capability type)
  - position_hint: above / inline / below (UI convention, not text-parsed)
  - modifiers: from residual tokens not consumed by ParamExtractor
  - relation_hints: between co-present capabilities

Ownership rule: structure layer ONLY interprets tokens NOT consumed by
ParamExtractor. This avoids double-semantic inflation.

No LLM. No new intents. No schema changes. Pure post-hoc annotation.
"""

from __future__ import annotations

import logging
from typing import Any

from app.graphir.intent import Intent

logger = logging.getLogger(__name__)


# ── Layout roles per capability (deterministic, UI convention) ───────

_LAYOUT_ROLES: dict[str, str] = {
    "presentation.kpi_row": "primary",
    "presentation.timeseries": "primary",
    "presentation.table": "secondary",
    "presentation.metric_card": "secondary",
}

# Capabilities with no layout_role annotation (layout.*, style.*, domain.*, etc.)
# will be skipped by annotate_structure.


# ── Fragment classification ─────────────────────────────────────────

_STRENGTHENERS: set[str] = {"top", "main", "latest", "recent"}
_NOISE: set[str] = {"add", "using", "data", "display", "price", "products", "tax"}


# ── Token normalization ─────────────────────────────────────────────


def _normalize(tokens: set[str]) -> set[str]:
    return {t.lower().strip() for t in tokens}


# ── Position hints (UI conventions, NOT text-parsed) ────────────────

_POSITION_HINTS: dict[str, str] = {
    "presentation.kpi_row": "above",
    "presentation.timeseries": "inline",
    "presentation.table": "below",
}


# ── Relation inference ──────────────────────────────────────────────


def _infer_relations(capability: str, has_kpi: bool, has_ts: bool, has_table: bool) -> list[dict]:
    hints: list[dict] = []
    if capability == "presentation.kpi_row" and has_ts:
        hints.append({"type": "feeds_into", "target_hint": "timeseries"})
    elif capability == "presentation.timeseries" and has_table:
        hints.append({"type": "summarizes_into", "target_hint": "table"})
    return hints


# ── Main entry point ────────────────────────────────────────────────


def annotate_structure(
    intents: list[Intent],
    unresolved: list[str],
    consumed_tokens: set[str] | None = None,
) -> list[Intent]:
    """Annotate each intent with structural context.

    Args:
        intents: Already-decomposed intents (from decompose_task).
        unresolved: Unresolved fragment tokens from decomposition.
        consumed_tokens: Tokens already consumed by ParamExtractor.
                         Tokens in this set will NOT be reinterpreted.

    Returns:
        New list of Intent objects with structure_context set where applicable.
    """
    if not intents:
        return intents

    consumed = _normalize(consumed_tokens or set())
    effective = _normalize(set(unresolved)) - consumed

    caps = {i.capability for i in intents}
    has_kpi = "presentation.kpi_row" in caps
    has_ts = "presentation.timeseries" in caps
    has_table = "presentation.table" in caps

    new_intents: list[Intent] = []
    for intent in intents:
        role = _LAYOUT_ROLES.get(intent.capability)
        if role is None:
            new_intents.append(intent)
            continue

        ctx: dict[str, Any] = {}

        # Step A: layout_role
        ctx["layout_role"] = role

        # Step B: position_hint (UI convention, NOT fragment-based)
        hint = _POSITION_HINTS.get(intent.capability)
        if hint:
            ctx["position_hint"] = hint

        # Step C: modifiers from effective (residual tokens only)
        modifiers = sorted((effective & _STRENGTHENERS) - _NOISE)
        if modifiers:
            ctx["modifiers"] = modifiers

        # Step D: relation_hints between detected capabilities
        hints = _infer_relations(intent.capability, has_kpi, has_ts, has_table)
        if hints:
            ctx["relation_hints"] = hints

        new_intents.append(Intent(
            id=intent.id,
            capability=intent.capability,
            params=intent.params,
            task_fragment=intent.task_fragment,
            weight=intent.weight,
            source=intent.source,
            structure_context=ctx,
        ))

    return new_intents
