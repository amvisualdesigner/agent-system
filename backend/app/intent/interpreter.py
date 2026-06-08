"""IntentInterpreter — único entrypoint LLM.

Lee capability_catalog.json (no el index).
Propone intención: contrato + capabilities + acciones + params.
NO concilia lifecycle (CREATE/MODIFY/DELETE).
NO escribe archivos.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.intent.models import InterpretationDraft
from app.intent.llm_client import llm_chat
from app.catalog.loader import load_catalog, list_contract_ids

logger = logging.getLogger(__name__)

# ── Instance hints for multi-instance capabilities ────────────────
# Mapea capability → {keyword_in_message → instance_hint}
# Usado para distinguir entre instancias cuando el usuario pide DELETE
# sobre una capability multi-instancia (e.g. LineChart vs Timeseries).

_INSTANCE_HINTS: dict[str, dict[str, str]] = {
    "presentation.timeseries": {
        "line": "linechart",
        "line chart": "linechart",
        "linechart": "linechart",
        "timeseries": "timeseries",
        "trend": "timeseries",
    },
}


def _resolve_instance_hints(
    actions: list[dict],
    message_lower: str,
) -> list[dict]:
    """Add instance_hint to actions for multi-instance capabilities.

    Solo aplica cuando el mensaje contiene palabras clave que distinguen
    instancias (e.g. "line" → LineChart vs "timeseries" → Timeseries).
    """
    result = []
    for action in actions:
        cap = action.get("target_capability", "")
        hints = _INSTANCE_HINTS.get(cap)
        if hints:
            for keyword, hint in hints.items():
                if keyword in message_lower:
                    action["instance_hint"] = hint
                    break
        result.append(action)
    return result

# ── Keyword overlap for contract selection ────────────────────────

_CONTRACT_KEYWORDS: dict[str, set[str]] = {
    "dashboard.sales_overview": {
        "sales", "dashboard", "kpi", "metric", "metrics", "revenue",
        "growth", "retention", "churn", "overview", "executive",
        "trend", "line chart", "timeseries", "kpi row",
    },
    "analytics.table": {
        "table", "data table", "columns", "tabular", "analytics table",
        "grid", "rows", "data grid",
    },
    "analytics.filter": {
        "filter", "facet", "filter panel", "refine", "filtering",
    },
    "analytics.chart_bar": {
        "bar chart", "barchart", "bar graph", "categories", "values",
    },
    "analytics.metric_card": {
        "metric card", "single metric", "value", "card",
    },
    "embed.external": {
        "embed", "iframe", "external", "widget",
    },
    "interaction.search": {
        "search", "search bar", "lookup", "find",
    },
    "interaction.form": {
        "form", "input", "fields", "submit",
    },
    "data.export": {
        "export", "download", "csv", "pdf",
    },
    "data.drilldown": {
        "drilldown", "drill down", "navigate", "detail",
    },
}

# ── Action verb detection ─────────────────────────────────────────

_ACTION_TRIGGERS: dict[str, list[str]] = {
    "remove": ["remove", "delete", "hide", "destroy", "drop", "clear", "eliminate"],
    "modify": ["modify", "update", "change", "set", "edit", "adjust", "replace", "configure"],
    "create": ["create", "add", "build", "generate", "compose", "design", "include", "insert"],
    "keep": ["keep", "maintain", "preserve", "leave"],
}

_VERB_OBJECT_PATTERNS: list[tuple[str, str, float]] = [
    # (verb, capability_id_prefix/keyword, priority)
    ("remove", "presentation.timeseries", 0.9),
    ("remove", "presentation.kpi_row", 0.9),
    ("remove", "presentation.table", 0.9),
    ("remove", "presentation.filter_panel", 0.9),
    ("remove", "presentation.chart.bar", 0.9),
    ("remove", "layout.page", 0.9),
    ("remove", "presentation.metric_card", 0.9),
    ("modify", "presentation.kpi_row", 0.9),
    ("modify", "presentation.timeseries", 0.9),
    ("modify", "presentation.table", 0.9),
    ("modify", "layout.page", 0.9),
]


# ── Contract selection (deterministic pre-filter) ─────────────────


def _score_contract(contract_id: str, message_lower: str) -> float:
    """Score how well a contract matches the user message by keyword overlap."""
    keywords = _CONTRACT_KEYWORDS.get(contract_id, set())
    if not keywords:
        return 0.0
    tokens = set(message_lower.split())
    overlap = tokens & keywords
    bigram_overlap = set()
    for kw in keywords:
        if " " in kw and kw in message_lower:
            bigram_overlap.add(kw)
    score = (len(overlap) + len(bigram_overlap)) / max(len(keywords), 1)
    return score


def _select_contract(message: str) -> str | None:
    """Select best contract by keyword overlap. Returns contract_id or None."""
    message_lower = message.lower().strip()
    if not message_lower:
        return None

    scores = {}
    for cid in _CONTRACT_KEYWORDS:
        scores[cid] = _score_contract(cid, message_lower)

    best = max(scores, key=scores.get) if scores else None
    best_score = scores.get(best, 0.0) if best else 0.0

    if best_score < 0.05:
        # No contract matched well enough — still try dashboard as default
        if any(t in message_lower for t in {"chart", "kpi", "metric", "dashboard", "line", "trend"}):
            return "dashboard.sales_overview"
        return None

    return best


def _has_action_verb(message: str) -> bool:
    """Check if message contains at least one known action verb."""
    lower = message.lower()
    for verbs in _ACTION_TRIGGERS.values():
        if any(v in lower for v in verbs):
            return True
    return False


def _detect_potential_multi_contract(
    message: str,
    contract_id: str,
    proposed_actions: list[dict],
) -> list[str]:
    """Detect if the user message suggests intents that span multiple contracts.

    Returns a list of warning strings, empty if no multi-contract issue detected.
    """
    lower = message.lower()
    warnings: list[str] = []

    # Quick check: no "and" or comma-separated actions → single intent
    has_conjunction = " and " in lower or ", " in lower or " & " in lower
    if not has_conjunction:
        return warnings

    # If only one proposed action but message has conjunctions, it's suspicious
    if len(proposed_actions) <= 1:
        # Score all contracts to see if multiple contracts match
        scores = {}
        for cid in _CONTRACT_KEYWORDS:
            scores[cid] = _score_contract(cid, lower)

        # Find contracts with meaningful overlap (not the selected one)
        other_contracts = [cid for cid, s in scores.items() if s > 0.15 and cid != contract_id]
        if len(other_contracts) >= 1:
            other_names = ", ".join(other_contracts)
            warnings.append(
                f"Your message mentions multiple requests that span different contracts "
                f"({other_names}). Currently only one contract can be handled at a time. "
                "Consider splitting into separate requests."
            )

    return warnings


# ── Build catalog slice for prompt ────────────────────────────────


def _build_catalog_slice(contract_id: str, catalog: dict) -> str:
    """Build a compact catalog description for the LLM prompt."""
    contracts = catalog.get("contracts", {})
    entry = contracts.get(contract_id)
    if not entry:
        return ""

    lines = [f"Contract: {contract_id}"]
    lines.append(f"  Label: {entry.get('label', '')}")
    lines.append(f"  Description: {entry.get('description', '')}")
    lines.append("  Capabilities:")

    for cap in entry.get("capabilities", []):
        cap_id = cap["id"]
        label = cap.get("label", cap_id)
        syns = ", ".join(cap.get("synonyms", []))
        verbs_block = ", ".join(
            f"{v}: [{', '.join(triggers)}]"
            for v, triggers in cap.get("verbs", {}).items()
        )
        lines.append(f"    - {cap_id} (label: '{label}')")
        if syns:
            lines.append(f"      synonyms: [{syns}]")
        if verbs_block:
            lines.append(f"      allowed_verbs: {{{verbs_block}}}")

    lines.append("  Examples:")
    for ex in entry.get("utterance_examples", []):
        lines.append(f"    - \"{ex['text']}\" → {json.dumps(ex['intent'])}")

    return "\n".join(lines)


def _build_system_prompt(catalog_slice: str) -> str:
    """Build the system prompt for the IntentInterpreter."""
    return f"""You are a BI dashboard intent interpreter. Your ONLY job is to map the user's natural language request to capabilities from the catalog below.

RULES:
1. ONLY use capabilities listed in the catalog below.
2. ONLY use verbs that are listed in each capability's allowed_verbs.
3. If the user mentions metrics (revenue, growth, etc.), include them in params.metrics.
4. If the user doesn't specify enough detail, set "clarification_needed" to true and suggest what's missing.
5. Return ONLY valid JSON — no markdown, no explanations, no extra text.
6. confidence is 0.0–1.0 based on how clearly the user expressed their intent.

CATALOG:
{catalog_slice}

OUTPUT JSON SCHEMA:
{{
  "contract_id": "str",
  "contract_version": 1,
  "actions": [
    {{
      "verb": "modify|remove|create|keep",
      "target_capability": "capability_id_from_catalog",
      "params": {{}},
      "confidence": 0.0
    }}
  ],
  "params": {{"metrics": []}},
  "confidence": 0.0,
  "clarification_needed": false,
  "clarification_question": null
}}
"""


# ── Post-LLM validation ──────────────────────────────────────────


def _validate_output(
    raw: dict,
    catalog_entry: dict,
    worktree_caps: list[dict],
) -> tuple[list[str], str | None]:
    """Validate LLM output against catalog.

    Returns (warnings, clarification_question).
    """
    warnings: list[str] = []
    cap_ids = {c["id"] for c in catalog_entry.get("capabilities", [])}
    cap_verbs: dict[str, set[str]] = {
        c["id"]: set(c.get("verbs", {}).keys())
        for c in catalog_entry.get("capabilities", [])
    }

    for action in raw.get("actions", []):
        cap = action.get("target_capability", "")
        verb = action.get("verb", "")

        if cap not in cap_ids:
            warnings.append(f"Unknown capability '{cap}' — not in catalog")
            action["target_capability"] = ""

        if verb and cap in cap_verbs and verb not in cap_verbs[cap]:
            allowed = cap_verbs[cap]
            # Verb not in allowed list - allow common synonyms via _ACTION_TRIGGERS
            verb_allowed = verb in allowed or any(
                verb in trigs for trigs in _ACTION_TRIGGERS.values()
            )
            if not verb_allowed:
                warnings.append(f"Verb '{verb}' not allowed for '{cap}' (allowed: {allowed})")

    # Check if actions reference capabilities that don't exist in worktree
    present_ids = {w["id"] for w in worktree_caps if w.get("present")}
    clarification = None
    for action in raw.get("actions", []):
        cap = action.get("target_capability", "")
        verb = action.get("verb", "")
        if verb in ("modify", "remove") and cap and cap not in present_ids:
            # It's possible the capability doesn't exist — warn but don't block
            pass

    return warnings, clarification


# ── Build worktree_capabilities from index snapshot ────────────────


def _build_worktree_caps(catalog_entry: dict, index_snapshot: dict | None) -> list[dict]:
    """Build worktree_capabilities list from catalog + index."""
    caps = []
    for cap in catalog_entry.get("capabilities", []):
        cap_id = cap["id"]
        present = False
        paths = []
        if index_snapshot:
            paths = index_snapshot.get(cap_id, [])
            present = len(paths) > 0
        caps.append({
            "id": cap_id,
            "label": cap.get("label", cap_id),
            "present": present,
            "paths": paths,
        })
    return caps


# ── Main interpreter function ──────────────────────────────────────


def interpret(
    message: str,
    conversation: list[dict] | None = None,
    index_snapshot: dict | None = None,
) -> InterpretationDraft:
    """Main entry point: interpret user message and return InterpretationDraft.

    Args:
        message: User's natural language request.
        conversation: Previous turns (max 2).
        index_snapshot: Optional snapshot from StructuralIndex (for worktree_capabilities).

    Returns:
        InterpretationDraft with status, proposed_actions, etc.
    """
    interpretation_id = str(uuid.uuid4())
    catalog = load_catalog()
    message_lower = message.lower()

    # 1. Select contract
    contract_id = _select_contract(message)
    if contract_id is None:
        return InterpretationDraft(
            interpretation_id=interpretation_id,
            status="unsupported",
            contract_id="",
            contract_version=0,
            proposed_actions=[],
            alternatives=[],
            params_proposed={},
            worktree_capabilities=[],
            clarification_question="No matching contract found for your request. Try 'sales dashboard', 'table', or 'chart'.",
        )

    # 2. Get catalog entry
    catalog_entry = catalog.get("contracts", {}).get(contract_id)
    if not catalog_entry:
        return InterpretationDraft(
            interpretation_id=interpretation_id,
            status="unsupported",
            contract_id=contract_id,
            contract_version=1,
            proposed_actions=[],
            alternatives=[],
            params_proposed={},
            worktree_capabilities=[],
            clarification_question=f"Contract '{contract_id}' not found in catalog.",
        )

    # 3. Build catalog slice for prompt
    catalog_slice = _build_catalog_slice(contract_id, catalog)
    system_prompt = _build_system_prompt(catalog_slice)

    # 4. Build worktree capabilities
    worktree_caps = _build_worktree_caps(catalog_entry, index_snapshot)
    worktree_block = "Worktree capabilities:\n" + json.dumps(worktree_caps, indent=2) if worktree_caps else ""

    # 5. Determine if clarification needed based on action verbs
    if not _has_action_verb(message):
        return InterpretationDraft(
            interpretation_id=interpretation_id,
            status="needs_clarification",
            contract_id=contract_id,
            contract_version=1,
            proposed_actions=[],
            alternatives=[],
            params_proposed={},
            worktree_capabilities=worktree_caps,
            clarification_question="I understand you're working with the dashboard. What would you like to do? (e.g., remove a chart, update metrics, modify the layout)",
        )

    # 6. Build user prompt with conversation context
    conv_block = ""
    if conversation:
        conv_lines = []
        for turn in conversation[-2:]:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            conv_lines.append(f"{role}: {content}")
        conv_block = "Conversation:\n" + "\n".join(conv_lines)

    user_prompt = f"""{conv_block}
Current message: {message}

{worktree_block}

Return valid JSON per the schema. If unclear, set clarification_needed=true.
"""

    # 7. Call LLM
    raw = llm_chat(user_prompt, system_prompt=system_prompt)
    logger.info("LLM interpret response: %s", json.dumps(raw, default=str)[:500])

    if "error" in raw:
        return InterpretationDraft(
            interpretation_id=interpretation_id,
            status="needs_clarification",
            contract_id=contract_id,
            contract_version=1,
            proposed_actions=[],
            alternatives=[],
            params_proposed={},
            worktree_capabilities=worktree_caps,
            clarification_question=f"I couldn't process that request. Please try rephrasing.",
        )

    # 8. Validate
    warnings, clarification = _validate_output(raw, catalog_entry, worktree_caps)
    if warnings:
        logger.warning("Interpret validation warnings: %s", warnings)

    # 9. Check if dry-run action map is empty
    clarification_needed = raw.get("clarification_needed", False)
    if clarification_needed:
        return InterpretationDraft(
            interpretation_id=interpretation_id,
            status="needs_clarification",
            contract_id=contract_id,
            contract_version=raw.get("contract_version", 1),
            proposed_actions=raw.get("actions", []),
            alternatives=[],
            params_proposed=raw.get("params", {}),
            worktree_capabilities=worktree_caps,
            clarification_question=raw.get("clarification_question", "Could you provide more detail?"),
            missing_mappings=warnings,
        )

    actions = raw.get("actions", [])
    if not actions:
        return InterpretationDraft(
            interpretation_id=interpretation_id,
            status="needs_clarification",
            contract_id=contract_id,
            contract_version=raw.get("contract_version", 1),
            proposed_actions=[],
            alternatives=[],
            params_proposed=raw.get("params", {}),
            worktree_capabilities=worktree_caps,
            clarification_question="I understand the contract but what action should I take? (e.g., remove, modify, create)",
        )

    # 10. Resolve instance hints for multi-instance capabilities
    actions = _resolve_instance_hints(actions, message_lower)

    # 11. Detect multi-contract intents
    multi_warnings = _detect_potential_multi_contract(message, contract_id, actions)
    if multi_warnings:
        warnings.extend(multi_warnings)

    # 12. Enrich proposed actions with labels (preserve instance_hint)
    cap_labels = {c["id"]: c.get("label", c["id"]) for c in catalog_entry.get("capabilities", [])}
    enriched = []
    for a in actions:
        cap = a.get("target_capability", "")
        enriched.append({
            "verb": a.get("verb", ""),
            "target_capability": cap,
            "label": cap_labels.get(cap, cap),
            "confidence": a.get("confidence", 0.8),
            "reason": f"{a.get('verb', '')} {cap_labels.get(cap, cap)}",
            "instance_hint": a.get("instance_hint"),
        })

    return InterpretationDraft(
        interpretation_id=interpretation_id,
        status="ok",
        contract_id=contract_id,
        contract_version=raw.get("contract_version", 1),
        proposed_actions=enriched,
        alternatives=[],
        params_proposed=raw.get("params", {}),
        worktree_capabilities=worktree_caps,
        clarification_question=clarification,
        missing_mappings=warnings,
    )
