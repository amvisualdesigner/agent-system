"""Intent Decomposition — task string → DecompositionResult.

Intent-first planning: decomposes a raw user task into explicit,
stable Intent objects that flow through the entire pipeline.

Phase 2: DecompositionResult separates detected vs inferred vs
unresolved, and decomposition_confidence tracks how well the
task was understood — independently of coverage.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.graphir.intent import Intent, make_intent_id, CAPABILITY_REGISTRY

logger = logging.getLogger(__name__)

# ── DecompositionResult ───────────────────────────────────────────


@dataclass
class DecompositionResult:
    """Structured result of intent decomposition.

    Separates *what was understood* (detected, inferred) from
    *what wasn't* (unresolved), plus an overall confidence that
    the decomposition captured the user's full intent.

    Fields:
        intents: The list of Intent objects flowing through the pipeline.
        decomposition_confidence: 0.0–1.0 — how well the task was understood.
        detected: capability IDs matched with high confidence (keywords).
        inferred: capability IDs matched with lower confidence (embedding/LLM).
        unresolved: tokens from the task that no pattern could map.
        original_task: The raw task string.
        ranked_candidates: Full ranked list from embedding (for traceability).
    """
    intents: list[Intent]
    decomposition_confidence: float = 1.0
    detected: list[str] = field(default_factory=list)
    inferred: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    original_task: str = ""
    ranked_candidates: list[tuple[str, float]] = field(default_factory=list)


# ── Keyword patterns ──────────────────────────────────────────────

# (keywords, capability, params_hint, fragment_hint, source_type)
# source_type: "detected" for keyword matches, "inferred" for weak matches
_TASK_PATTERNS: list[tuple[list[str], str, dict[str, Any], str, str]] = [
    # ── Presentation (domain-agnostic visual components) ──
    (["kpi", "metric", "metrics", "key performance"], "presentation.kpi_row", {}, "kpi metrics", "detected"),
    (["timeseries", "time series", "trend", "over time", "chart"], "presentation.timeseries", {}, "timeseries", "detected"),
    (["table", "tabular", "grid", "data table", "analytics table"], "presentation.table", {}, "table", "detected"),
    (["filter", "facet", "refine"], "presentation.filter_panel", {}, "filters", "detected"),
    (["bar chart", "bar graph", "bars"], "presentation.chart.bar", {}, "bar chart", "detected"),
    (["metric card", "single metric", "number"], "presentation.metric_card", {}, "metric card", "detected"),
    (["embed", "external", "iframe"], "presentation.embed", {}, "embed", "detected"),

    # ── Domain (semantic enrichment) ──
    (["analytics", "analytical", "business intelligence"], "domain.analytics", {}, "analytics domain", "detected"),
    (["sales", "revenue", "deals", "pipeline", "growth"], "domain.sales", {}, "sales domain", "detected"),

    # ── Layout (structural — soft) ──
    (["page", "dashboard", "screen", "view"], "layout.page", {}, "page layout", "detected"),
    (["grid layout", "grid"], "layout.grid", {}, "grid layout", "detected"),
    (["container", "section", "panel"], "layout.container", {}, "container", "detected"),

    # ── Style (stylistic — soft) ──
    (["dark", "dark theme", "dark mode"], "style.theme.dark", {}, "dark theme", "detected"),
    (["light", "light theme", "light mode"], "style.theme.light", {}, "light theme", "detected"),
    (["enterprise", "corporate", "professional"], "style.theme.enterprise", {}, "enterprise theme", "detected"),
    (["elevated", "shadow", "card elevated"], "style.card.elevated", {}, "elevated card", "detected"),

    # ── Interaction ──
    (["search", "lookup"], "interaction.search", {}, "search", "detected"),
    (["form", "input", "submit"], "interaction.form", {}, "form", "detected"),

    # ── Data ──
    (["export", "download", "csv"], "data.export", {}, "data export", "detected"),
    (["drilldown", "drill down", "details"], "data.drilldown", {}, "drilldown", "detected"),
]


def _compute_unresolved(task: str, matched_keywords: set[str]) -> list[str]:
    """Find tokens in the task that no pattern matched.

    A token is 'unresolved' if it isn't a stopword and wasn't
    part of any matched keyword phrase.
    """
    STOPWORDS = {"a", "an", "the", "and", "or", "but", "in", "on", "at",
                 "to", "for", "of", "with", "by", "from", "is", "are",
                 "was", "were", "be", "been", "being", "create", "have", "has",
                 "had", "do", "does", "did", "will", "would", "could",
                  "should", "showing", "may", "might", "shall", "can", "need",
                 "da", "que", "en", "el", "la", "los", "las", "un",
                 "una", "con", "para", "por", "al", "del", "lo",
                 "se", "no", "es", "como", "más", "pero", "sus",
                 "le", "ya", "este", "entre", "porque", "todo",
                 "esta", "sin", "ella", "ello", "cada", "otro",
                 "ser", "haber", "tener", "hacer", "estar",
                 "very", "just", "only", "also", "so", "if",
                 "then", "than", "that", "this", "these", "those",
                 "i", "me", "my", "myself", "we", "our", "ours",
                 "you", "your", "yours", "he", "him", "his",
                 "she", "her", "hers", "it", "its", "they", "them",
                 "their", "theirs", "what", "which", "who", "whom",
                 "when", "where", "why", "how", "all", "each",
                 "some", "any", "both", "few", "more", "most",
                 "other", "such", "not", "nor", "too"}

    tokens = set(re.findall(r'\w+', task.lower()))
    meaningful = tokens - STOPWORDS
    return sorted(meaningful - matched_keywords)


def _keyword_decompose(task: str) -> DecompositionResult:
    """Deterministic keyword-based decomposition.

    All keyword matches are 'detected'. Unmatched meaningful
    tokens are 'unresolved'.
    """
    task_lower = task.lower()
    seen: set[str] = set()
    intents: list[Intent] = []
    detected: list[str] = []
    all_matched_keywords: set[str] = set()

    for keywords, capability, params_hint, fragment_hint, source_type in _TASK_PATTERNS:
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
            source="keyword",
        ))
        detected.append(capability)
        # Track which keyword words were matched for unresolved computation
        for kw in matched:
            all_matched_keywords.update(kw.split())

    # Compute decomposition confidence
    decomposition_confidence = compute_decomposition_confidence(task, intents)

    # Compute unresolved tokens
    unresolved = _compute_unresolved(task, all_matched_keywords)

    return DecompositionResult(
        intents=intents,
        decomposition_confidence=decomposition_confidence,
        detected=detected,
        inferred=[],
        unresolved=unresolved,
        original_task=task,
    )


def compute_decomposition_confidence(task: str, intents: list[Intent]) -> float:
    """Compute confidence that decomposition captured all user intent.

    Based on token coverage: what fraction of the task's meaningful
    tokens were matched by intent fragments.

    Returns 0.0–1.0:
      1.0 = every token matched
      0.0 = no tokens matched
    """
    if not task or not task.strip():
        return 1.0
    if not intents:
        return 0.0

    task_lower = task.lower()
    tokens = set(re.findall(r'\w+', task_lower))
    if not tokens:
        return 1.0

    matched_tokens: set[str] = set()
    for intent in intents:
        fragment = intent.task_fragment.lower() if intent.task_fragment else ""
        if fragment:
            matched_tokens.update(re.findall(r'\w+', fragment))

    if not matched_tokens:
        return 0.0

    overlap = len(tokens & matched_tokens)
    return overlap / len(tokens) if tokens else 1.0


def _embedding_enhance(result: DecompositionResult) -> DecompositionResult:
    """Enhance a DecompositionResult with embedding-based capability ranking.

    Runs embedding ranking on the original task, adding top-k matches
    (not already detected) to the `inferred` list. Does NOT modify
    `decomposition_confidence` — that remains keyword-based.

    This is a rank-only enhancement: keywords are the gatekeeper,
    embeddings only suggest additional candidates.

    Traceability:
      - Inferred intents have source="embedding" and weight=cosine_score.
      - ranked_candidates stores the full ranked list.
    """
    if not result.original_task or not result.intents:
        return result

    from app.graphir.intent_embedding import rank_capabilities

    already_detected = set(result.detected)
    ranked = rank_capabilities(result.original_task, exclude=already_detected)

    if not ranked:
        return result

    # Store full ranked list for traceability
    result.ranked_candidates = ranked

    new_inferred: list[str] = []
    existing_caps = {i.capability for i in result.intents}

    for cap_id, score in ranked:
        if cap_id in existing_caps:
            continue
        fid = make_intent_id(f"embedding:{cap_id}", cap_id, "embedding")
        result.intents.append(Intent(
            id=fid,
            capability=cap_id,
            task_fragment=f"embedding:{cap_id}",
            weight=score,
            source="embedding",
        ))
        new_inferred.append(cap_id)
        existing_caps.add(cap_id)

    if new_inferred:
        result.inferred = new_inferred

    return result


def decompose_task(task: str, use_llm: bool = False, use_embedding: bool | None = None) -> DecompositionResult:
    """Decompose a raw task string into a structured DecompositionResult.

    Args:
        task: Raw user task string.
        use_llm: If True, attempt LLM-based decomposition first
                 (falls back to keyword if LLM unavailable).
        use_embedding: If True, enhance keyword decomposition with
                       embedding-based ranking. Defaults to
                       settings.EMBEDDING_ENABLED.

    Returns:
        DecompositionResult with intents, confidence, and metadata.
    """
    if not task or not task.strip():
        return DecompositionResult(
            intents=[],
            decomposition_confidence=1.0,
            detected=[],
            inferred=[],
            unresolved=[],
            original_task=task or "",
        )

    if use_llm:
        try:
            return _llm_decompose(task)
        except Exception as e:
            logger.warning("LLM decomposition failed, falling back to keyword: %s", e)

    result = _keyword_decompose(task)

    # Embedding enhancement (rank-only, keywords are gatekeeper)
    if use_embedding is None:
        from app.config.settings import settings as _settings
        use_embedding = _settings.EMBEDDING_ENABLED

    if use_embedding:
        try:
            result = _embedding_enhance(result)
        except Exception as e:
            logger.warning("Embedding enhancement failed (non-fatal): %s", e)

    # Param extraction (enrich intents with structured params from task)
    consumed_tokens: set[str] = set()
    try:
        result, consumed_tokens = _enrich_params(result)
    except Exception as e:
        logger.warning("Param extraction failed (non-fatal): %s", e)

    # Structural annotation (post-hoc, uses residual tokens not consumed by params)
    from app.graphir.intent_structure import annotate_structure
    try:
        result.intents = annotate_structure(result.intents, result.unresolved, consumed_tokens)
    except Exception as e:
        logger.warning("Structure annotation failed (non-fatal): %s", e)

    return result


def _enrich_params(result: DecompositionResult) -> tuple[DecompositionResult, set[str]]:
    """Enrich each intent's params based on its param_schema.

    Uses ParamExtractor (rule-based) to extract metrics, dimensions,
    top_k, etc. from the original task string.

    Returns:
        (updated DecompositionResult, consumed_tokens from ParamExtractor)
    """
    from app.graphir.param_extractor import ParamExtractor
    extractor = ParamExtractor()
    new_intents: list[Intent] = []
    for intent in result.intents:
        contract = CAPABILITY_REGISTRY.get(intent.capability)
        if contract and contract.param_schema:
            extracted = extractor.extract(result.original_task, intent, contract)
            if extracted:
                new_intents.append(Intent(
                    id=intent.id,
                    capability=intent.capability,
                    params={**intent.params, **extracted},
                    task_fragment=intent.task_fragment,
                    weight=intent.weight,
                    source=intent.source,
                ))
                continue
        new_intents.append(intent)
    result.intents = new_intents
    return result, extractor.consumed_tokens


def _llm_decompose(task: str) -> DecompositionResult:
    """LLM-based intent decomposition.

    Uses the configured LLM to semantically decompose the task.
    Falls back to keyword-based if the LLM is unreachable.
    """
    from app.config.settings import settings
    import httpx

    prompt = (
        "You are an intent decomposition system. Given a user task, "
        "return a JSON array of intents. Each intent has:\n"
        "  - capability: one of the known capabilities\n"
        "  - params: dict of parameter key-value pairs\n"
        "  - task_fragment: verbatim substring from the task\n\n"
        "Known capabilities: presentation.kpi_row, presentation.timeseries, "
        "presentation.table, presentation.filter_panel, "
        "presentation.chart.bar, presentation.metric_card, presentation.embed, "
        "domain.analytics, domain.sales, "
        "data.export, data.drilldown, "
        "layout.page, layout.container, layout.grid, "
        "style.theme.dark, style.theme.light, style.theme.enterprise, style.card.elevated, "
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
    detected: list[str] = []
    all_matched_keywords: set[str] = set()

    for i, item in enumerate(raw):
        cap = item.get("capability", "")
        tf = item.get("task_fragment", "")
        params = item.get("params", {})
        fid = make_intent_id(tf, cap, f"llm_{i}")
        intents.append(Intent(id=fid, capability=cap, params=params, task_fragment=tf, source="llm"))
        detected.append(cap)
        if tf:
            all_matched_keywords.update(tf.split())

    decomposition_confidence = compute_decomposition_confidence(task, intents)
    unresolved = _compute_unresolved(task, all_matched_keywords)

    return DecompositionResult(
        intents=intents,
        decomposition_confidence=decomposition_confidence,
        detected=detected,
        inferred=[],
        unresolved=unresolved,
        original_task=task,
    )
