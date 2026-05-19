"""Intent Decomposition — task string → list[Intent].

Intent-first planning: decomposes a raw user task into explicit,
stable Intent objects that flow through the entire pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

from app.graphir.intent import Intent, make_intent_id

logger = logging.getLogger(__name__)

# ── Keyword-based decomposition (deterministic fallback) ───────────

_TASK_PATTERNS: list[tuple[list[str], str, dict[str, Any], str]] = [
    # Keywords → (capability, params_hint, task_fragment_hint)
    (["kpi", "metric", "metrics", "key performance"], "display.kpi_row", {}, "kpi metrics"),
    (["timeseries", "time series", "trend", "over time", "chart"], "display.timeseries", {}, "timeseries"),
    (["table", "tabular", "grid", "data table", "analytics table"], "display.analytics_table", {}, "table"),
    (["filter", "facet", "refine"], "display.filter_panel", {}, "filters"),
    (["page", "dashboard", "screen", "view"], "layout.page", {}, "page"),
    (["search", "lookup"], "interaction.search", {}, "search"),
    (["form", "input", "submit"], "interaction.form", {}, "form"),
    (["embed", "external", "iframe"], "embed.external", {}, "embed"),
]


def _keyword_decompose(task: str) -> list[Intent]:
    """Deterministic keyword-based decomposition (LLM fallback)."""
    task_lower = task.lower()
    seen: set[str] = set()
    intents: list[Intent] = []

    for keywords, capability, params_hint, fragment_hint in _TASK_PATTERNS:
        matched = [kw for kw in keywords if kw in task_lower]
        if not matched:
            continue
        if capability in seen:
            continue
        seen.add(capability)
        fid = make_intent_id(fragment_hint, capability, "keyword")
        intents.append(Intent(
            id=fid,
            capability=capability,
            params=dict(params_hint),
            task_fragment=fragment_hint,
        ))

    return intents


def decompose_task(task: str, use_llm: bool = False) -> list[Intent]:
    """Decompose a raw task string into stable Intent objects.

    Args:
        task: Raw user task string.
        use_llm: If True, attempt LLM-based decomposition first
                 (falls back to keyword if LLM unavailable).

    Returns:
        list[Intent]: decomposed intents with stable IDs.
                      Empty list if no intents could be identified.
    """
    if not task or not task.strip():
        return []

    if use_llm:
        try:
            return _llm_decompose(task)
        except Exception as e:
            logger.warning("LLM decomposition failed, falling back to keyword: %s", e)

    return _keyword_decompose(task)


def _llm_decompose(task: str) -> list[Intent]:
    """LLM-based intent decomposition.

    Uses the configured LLM to semantically decompose the task.
    Falls back to keyword-based if the LLM is unreachable.

    Returns:
        list[Intent]: decomposed intents with stable IDs.
    """
    from app.config.settings import settings
    import httpx

    prompt = (
        "You are an intent decomposition system. Given a user task, "
        "return a JSON array of intents. Each intent has:\n"
        "  - capability: one of the known capabilities\n"
        "  - params: dict of parameter key-value pairs\n"
        "  - task_fragment: verbatim substring from the task\n\n"
        "Known capabilities: display.kpi_row, display.timeseries, "
        "display.analytics_table, display.filter_panel, "
        "embed.external, data.export, data.drilldown, "
        "layout.page, layout.container, layout.grid, "
        "interaction.search, interaction.form\n\n"
        f"Task: {task}\n\n"
        "Return ONLY a valid JSON array, no other text."
    )

    with httpx.Client(timeout=30) as client:
        r = client.post(
            f"{settings.LLM_BASE_URL}/v1/chat/completions",
            json={
                "model": settings.LLM_MODEL,
                "messages": [
                    {"role": "system", "content": "You are an intent decomposition system. Return only JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
            },
        )
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"].strip()
        import json
        raw = json.loads(content)

    intents: list[Intent] = []
    for i, item in enumerate(raw):
        cap = item.get("capability", "")
        tf = item.get("task_fragment", "")
        params = item.get("params", {})
        fid = make_intent_id(tf, cap, f"llm_{i}")
        intents.append(Intent(id=fid, capability=cap, params=params, task_fragment=tf))

    return intents
