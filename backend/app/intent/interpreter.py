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
from app.contracts.skill_registry import get_contract

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
        "filtro", "filtrar", "filtros", "panel de filtros", "facetar",
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
    "create": [
        "create", "add", "build", "generate", "compose", "design", "include", "insert",
        "filter", "filtering", "refine", "filtro", "filtrar", "filtra", "filtros", "facetar",
    ],
    "keep": ["keep", "maintain", "preserve", "leave"],
    "transform": ["transform", "swap", "migrate", "convert", "morph", "substitute"],
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
    ("transform", "presentation.chart.bar", 0.9),
    ("transform", "presentation.timeseries", 0.9),
    ("transform", "presentation.table", 0.9),
]


# ── Contract selection (intent-first, catalog-derived) ─────────────
#
# Fuente de verdad: capability_catalog.json. Cada capability tiene un rol
# estructural por `graphir_type`: Page/Domain son CONTEXTO (destino/target,
# p.ej. "sales dashboard"), el resto es INTENCION (p.ej. add/modify filter).
# La intencion explicita de la capability gana SIEMPRE al contexto: un filtro
# que se anade EN el dashboard selecciona analytics.filter, no el dashboard.

# graphir_types semanticos SIN intencion propia (solo contexto/target).
_CONTEXT_GRAPHIR_TYPES = frozenset({"Page", "Domain"})


def _is_context_capability(capability: dict) -> bool:
    gtype = (capability.get("graphir_type") or "").strip()
    return gtype in _CONTEXT_GRAPHIR_TYPES


def _capability_terms(capability: dict) -> list[str]:
    """Vocabulario de la capability: label + synonyms del catalogo."""
    terms: list[str] = []
    label = (capability.get("label") or "").strip()
    if label:
        terms.append(label)
    terms.extend(t for t in (capability.get("synonyms") or []) if t)
    return terms


def _capability_select_score(capability: dict, message_lower: str, tokens: set[str]) -> float:
    """Affinidad lexical entre una capability y el mensaje (catalogo, no keywords).

    - terminos multi-word → substring exacto en el mensaje
    - tokens simples → token exacto en el mensaje
    - overlap parcial de multi-word → aporte menor
    """
    score = 0.0
    for term in _capability_terms(capability):
        t = str(term).lower().strip()
        if not t:
            continue
        if " " in t:
            if t in message_lower:
                score += 0.8
            else:
                parts = {p for p in t.split() if len(p) > 2}
                if parts and (parts & tokens):
                    score += 0.25
        elif t in tokens:
            score += 0.6
    return score


def _score_contract(contract_id: str, message_lower: str) -> float:
    """Legacy keyword-overlap score (densidad por keyword set).

    Se mantiene como fallback deterministico y para deteccion multi-contract
    (_detect_potential_multi_contract). Ya NO es la seleccion primaria.
    """
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


def _select_contract_legacy(message_lower: str) -> str | None:
    """Seleccion clasica por overlap de keywords (conservada intacta)."""
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


def _select_contract(message: str) -> str | None:
    """Selecciona el contrato intent-first desde el catalogo. contract_id o None.

    Reglas:
      1. intenciones explicitas (graphir_type != Page/Domain) por affinidad;
         si hay N, gana la mejor (determinista: score > label > contract_id).
      2. SI existe una intencion, el contexto NUNCA la derrota: por eso
         "Add a filter by size in sales dashboard" selecciona analytics.filter.
      3. sin intencion pero con contexto conocido (p.ej. dashboard) → el
         contrato de ese contexto (superficie previa: "Update dashboard").
      4. sin ninguna evidencia → fallback clasico (_CONTRACT_KEYWORDS).
    """
    message_lower = message.lower().strip()
    if not message_lower:
        return None

    catalog = load_catalog()
    tokens = set(message_lower.split())

    intents: list[tuple[float, str, str]] = []
    contexts: list[tuple[float, str, str]] = []
    for contract_id, entry in (catalog.get("contracts", {}) or {}).items():
        for capability in entry.get("capabilities") or []:
            score = _capability_select_score(capability, message_lower, tokens)
            if score <= 0:
                continue
            label = capability.get("label") or capability.get("id") or ""
            rank = (score, label, contract_id)
            if _is_context_capability(capability):
                contexts.append(rank)
            else:
                intents.append(rank)

    if intents:
        intents.sort(key=lambda r: (-r[0], r[1], r[2]))
        return intents[0][2]

    if contexts:
        contexts.sort(key=lambda r: (-r[0], r[1], r[2]))
        return contexts[0][2]

    return _select_contract_legacy(message_lower)


def _has_action_verb(message: str) -> bool:
    """Check if message contains at least one known action verb."""
    lower = message.lower()
    for verbs in _ACTION_TRIGGERS.values():
        if any(v in lower for v in verbs):
            return True
    return False


def _detect_verbs(message: str) -> list[str]:
    """Deterministically detect which action verbs the message expresses."""
    lower = message.lower()
    return [
        verb for verb, triggers in _ACTION_TRIGGERS.items()
        if any(t in lower for t in triggers)
    ]


def _capability_verb_score(capability: dict, message_lower: str) -> float:
    """Deterministic lexical affinity between a catalog capability and the message."""
    terms = [capability.get("label", "") or "", capability.get("id", "") or ""]
    terms += list(capability.get("synonyms", []) or [])
    score = 0.0
    for term in terms:
        term_lower = str(term).lower().strip()
        if not term_lower:
            continue
        if term_lower in message_lower:
            score += 0.6
            continue
        tokens = {t for t in term_lower.replace("_", " ").replace(".", " ").split() if len(t) > 2}
        if tokens and tokens & set(message_lower.split()):
            score += 0.3
    return score


def _build_semantic_alternatives(
    contract_id: str,
    contract_version: int,
    catalog_entry: dict,
    message: str,
    max_options: int = 3,
) -> list[dict]:
    """Structured semantic alternatives (Fase 5A / G8).

    Purely deterministic: detected verbs × catalog capabilities, ranked by
    lexical affinity. This is the SEMANTIC ambiguity surface and is distinct
    from `instance_choices` (structural index) and `choices` (page context).
    Empty when the message does not express ≥2 plausible capabilities.
    """
    verbs = _detect_verbs(message)
    if not verbs:
        return []

    message_lower = message.lower()
    ranked: list[tuple[float, str, dict, str]] = []
    for cap in catalog_entry.get("capabilities", []):
        cap_verbs = set((cap.get("verbs") or {}).keys())
        matched = [v for v in verbs if v in cap_verbs]
        if not matched:
            continue
        verb = matched[0]
        score = _capability_verb_score(cap, message_lower)
        # Only lexically plausible candidates: a capability the message says
        # nothing about is not an "alternative", it is noise.
        if score <= 0:
            continue
        ranked.append((score, cap["id"], cap, verb))

    if len(ranked) < 2:
        return []

    # Deterministic order: score desc, then capability id asc.
    ranked.sort(key=lambda r: (-r[0], r[1]))

    alternatives = []
    for idx, (score, cap_id, cap, verb) in enumerate(ranked[:max_options]):
        label = cap.get("label", cap_id)
        alternatives.append({
            "id": f"alt_{idx}",
            "kind": "capability",
            "label": f"{verb} {label}".strip(),
            "description": (
                f"{cap.get('description') or label}"
                if cap.get("description") else label
            ),
            "contract_id": contract_id,
            "contract_version": contract_version,
            "score": round(score, 3),
            "proposed_actions": [{
                "verb": verb,
                "target_capability": cap_id,
                "confidence": max(0.5, round(min(score, 1.0), 3)),
            }],
        })
    return alternatives


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

VERBS:
- "modify" — update an existing capability (change metrics, layout, etc.)
- "remove" — delete a capability from the dashboard
- "create" — add a new capability to the dashboard
- "keep" — leave as-is, no changes
- "transform" — replace ONE capability with ANOTHER (e.g. "replace line chart with bar chart").
  For transform actions, put the SOURCE capability in the top-level field source_capability.
  Example: {{"verb": "transform", "source_capability": "presentation.timeseries", "target_capability": "presentation.chart.bar"}}

OUTPUT JSON SCHEMA:
{{
  "contract_id": "str",
  "contract_version": 1,
  "actions": [
    {{
      "verb": "modify|remove|create|keep|transform",
      "target_capability": "capability_id_from_catalog",
      "source_capability": "capability_id_from_catalog (only for transform)",
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
        src = action.get("source_capability", "")

        if cap not in cap_ids:
            warnings.append(f"Unknown capability '{cap}' — not in catalog")
            action["target_capability"] = ""

        if verb in ("transform",) and not src:
            warnings.append("Transform action without source_capability — specify what is being replaced")
        elif src and src not in cap_ids:
            warnings.append(f"Unknown source_capability '{src}' — not in catalog")
            action["source_capability"] = ""

        if verb and cap in cap_verbs and verb not in cap_verbs[cap]:
            allowed = cap_verbs[cap]
            # Verb not in allowed list - allow common synonyms via _ACTION_TRIGGERS
            verb_allowed = verb in allowed or any(
                verb in trigs for trigs in _ACTION_TRIGGERS.values()
            )
            if not verb_allowed:
                warnings.append(f"Verb '{verb}' not allowed for '{cap}' (allowed: {allowed})")

    # Check if actions reference capabilities that don't exist in worktree.
    # D1: emit an observable, deterministic warning when MODIFY/REMOVE targets
    # an absent capability. Never converts to CREATE, never retargets, never
    # blocks — RepositoryValidation remains the decision authority.
    present_ids = {w["id"] for w in worktree_caps if w.get("present")}
    existence_verbs = set(_ACTION_TRIGGERS["modify"]) | set(_ACTION_TRIGGERS["remove"])
    for action in raw.get("actions", []):
        cap = action.get("target_capability", "")
        verb = (action.get("verb") or "").lower()
        if not cap or not verb or cap not in cap_ids:
            continue
        if cap in present_ids or verb not in existence_verbs:
            continue
        warnings.append(
            f"Capability '{cap}' not present in repository context — "
            f"'{verb}' targets an absent capability and may conflict at RepositoryValidation"
        )

    return warnings, None


def _validate_params(params: dict, contract_id: str) -> list[str]:
    """Valida params propuestos contra el input_schema del contrato.

    Deterministico; la clasificacion resultante es clarification_needed,
    NUNCA un cambio de contrato. Restricciones soportadas:
      - type==array: enum de items, o tipo string obligatorio
      - type==string: enum
    Parametros desconocidos o ausentes del schema no bloquean (son libres).
    """
    if not isinstance(params, dict):
        return [f"params must be an object, got {type(params).__name__}"]

    contract = get_contract(contract_id, 1)
    if contract is None:
        return []

    props = ((contract.input_schema or {}).get("properties") or {})
    errors: list[str] = []
    for key, value in params.items():
        prop = props.get(key)
        if prop is None or value is None:
            continue
        ptype = prop.get("type")
        if ptype == "array":
            if not isinstance(value, list):
                errors.append(f"Parameter '{key}' must be an array")
                continue
            items = prop.get("items") or {}
            enum = items.get("enum") or []
            if enum:
                invalid = [v for v in value if v not in enum]
                if invalid:
                    errors.append(
                        f"Parameter '{key}' has invalid value(s): "
                        f"{', '.join(map(str, invalid))} "
                        f"(valid: {', '.join(map(str, enum))})"
                    )
            elif items.get("type") == "string":
                non_str = [v for v in value if not isinstance(v, str)]
                if non_str:
                    errors.append(f"Parameter '{key}' must contain only strings")
        elif ptype == "string" and isinstance(value, str):
            enum = prop.get("enum") or []
            if enum and value not in enum:
                errors.append(
                    f"Parameter '{key}' has invalid value '{value}' "
                    f"(valid: {', '.join(map(str, enum))})"
                )
    return errors


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
            status="clarification_needed",
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
            status="clarification_needed",
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

    # 8b. Param validation vs contract input_schema (enum/type). Deterministic;
    #     classification is clarification_needed, NEVER a contract switch.
    param_errors = _validate_params(raw.get("params") or {}, contract_id)
    if param_errors:
        warnings.extend(param_errors)
        if not raw.get("clarification_needed"):
            raw["clarification_needed"] = True
            raw["clarification_question"] = (
                "Some parameters need attention: " + "; ".join(param_errors)
                + ". Please provide valid values or rephrase."
            )

    # 9. Check if dry-run action map is empty
    clarification_needed = raw.get("clarification_needed", False)
    if clarification_needed:
        return InterpretationDraft(
            interpretation_id=interpretation_id,
            status="clarification_needed",
            contract_id=contract_id,
            contract_version=raw.get("contract_version", 1),
            proposed_actions=raw.get("actions", []),
            alternatives=_build_semantic_alternatives(
                contract_id, raw.get("contract_version", 1), catalog_entry, message,
            ),
            params_proposed=raw.get("params", {}),
            worktree_capabilities=worktree_caps,
            clarification_question=raw.get("clarification_question", "Could you provide more detail?"),
            missing_mappings=warnings,
        )

    actions = raw.get("actions", [])
    if not actions:
        return InterpretationDraft(
            interpretation_id=interpretation_id,
            status="clarification_needed",
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
        src = a.get("source_capability", "")
        enriched.append({
            "verb": a.get("verb", ""),
            "target_capability": cap,
            "source_capability": src or None,
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
        alternatives=_build_semantic_alternatives(
            contract_id, raw.get("contract_version", 1), catalog_entry, message,
        ),
        params_proposed=raw.get("params", {}),
        worktree_capabilities=worktree_caps,
        clarification_question=clarification,
        missing_mappings=warnings,
    )
