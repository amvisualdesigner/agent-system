import json
import logging
import re

from app.contracts.skill_ir import SkillIR
from app.contracts.skill_registry import contract_exists
from app.planner.plan_generator import call_llm

logger = logging.getLogger(__name__)

_DOMAIN_KEYWORDS = {
    "sales", "revenue", "dashboard", "kpi", "metric", "metrics", "analytics",
    "chart", "growth", "retention", "churn", "timeseries",
    "performance", "business", "report", "overview",
    "table", "tabular", "grid", "columns", "rows", "data",
}

SKILL_IR_SYSTEM = (
    "You are a SkillIR Planner. Choose between these contracts:\n"
    '- "dashboard.sales_overview": business metrics dashboard\n'
    '- "analytics.table": data table with columns and rows\n'
    '- "noop": no contract matches the task\n\n'
    "Return ONLY valid JSON: {\"contract_id\": \"str\", \"version\": 1, \"params\": {}, \"confidence\": 0.0}\n"
    "No markdown, no explanations, no extra text."
)


def _clean_json(text: str) -> str:
    text = text.strip()
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        text = text[first:last + 1]
    return text


def _token_score(task: str, domain_keywords: set) -> float:
    task_tokens = set(task.lower().split())
    overlap = task_tokens & domain_keywords
    return len(overlap) / max(len(task_tokens), 1)


def _fusion_conf(llm_conf: float, tok_score: float) -> float:
    return min(0.7 * llm_conf + 0.3 * tok_score, 0.95)


def generate_skill_ir(task: str, task_mode: str) -> SkillIR:
    prompt = f"""Task: {task}

STEP 1 — Decide relevance (check in this order):

1. Is this task about a TABLE, DATA TABLE, ANALYTICS TABLE, TABULAR DATA, or COLUMNS?
   - YES → use contract_id="analytics.table", version=1

2. Is this task about BUSINESS METRICS, SALES KPIs, DASHBOARD, or REVENUE?
   - YES → use contract_id="dashboard.sales_overview", version=1

3. Otherwise → use contract_id="noop"

STEP 2 — Extract parameters (only for matching contract):

For analytics.table:
- "params" -> "columns": list of column names (e.g. ["Metric", "Value", "Change"])
- "params" -> "table_data": optional list of rows, each row a list matching column order

For dashboard.sales_overview:
- "params" -> "metrics": list from ["revenue", "growth", "retention", "churn"]
- "params" -> "timeseries_metric": one from ["revenue", "growth", "retention"] (optional, default "revenue")
- Do NOT rename fields: use "metrics" not "kpi_metrics"
- Do NOT invent values: only the listed enum strings allowed

Return ONLY valid JSON — no markdown, no extra text.
"""

    llm_result = call_llm(prompt, system_prompt=SKILL_IR_SYSTEM)

    if "error" in llm_result:
        raw = llm_result.get("raw", "")
        if raw:
            raw = _clean_json(raw)
            try:
                parsed = json.loads(raw)
                llm_result = parsed
            except Exception:
                pass

    if isinstance(llm_result, dict) and "error" in llm_result:
        logger.warning(f"LLM error: {llm_result.get('error')}")
        return SkillIR(contract_id=None, version=1, params={}, confidence=0.0)

    raw = llm_result
    if isinstance(raw, dict) and "actions" in raw:
        logger.warning("LLM returned legacy actions format")
        raw = {"contract_id": "noop", "version": 1, "params": {}, "confidence": 0.0}

    if isinstance(raw, dict) and "contract_id" not in raw:
        logger.warning(f"LLM result missing contract_id, keys: {list(raw.keys())}")
        raw = {"contract_id": "noop", "version": 1, "params": {}, "confidence": 0.0}

    try:
        parsed = SkillIR.from_dict(raw)
        if not parsed.is_valid():
            logger.warning(f"SkillIR invalid: {raw}")
            return SkillIR(contract_id=None, version=1, params={}, confidence=0.0)

        if parsed.contract_id not in (None, "noop") and not contract_exists(parsed.contract_id):
            logger.warning(f"contract_id={parsed.contract_id} not in registry, treating as noop")
            return SkillIR(contract_id=None, version=1, params={}, confidence=0.0)

        # NOOP contract: treat as noop
        if parsed.contract_id == "noop":
            return SkillIR(contract_id=None, version=1, params={}, confidence=0.0)

        # Scoring fusion: LLM proposes, rules calibrate
        llm_conf = parsed.confidence
        if llm_conf < 0.01:
            llm_conf = 0.1

        # Domain prior penalty: if no domain keyword in task, halve LLM confidence
        if not any(k in task.lower() for k in _DOMAIN_KEYWORDS):
            llm_conf *= 0.5

        tok_score = _token_score(task, _DOMAIN_KEYWORDS)
        parsed.confidence = _fusion_conf(llm_conf, tok_score)

        logger.info(f"SkillIR: contract_id={parsed.contract_id}, confidence={parsed.confidence}")
        return parsed
    except Exception as e:
        logger.warning(f"SkillIR parse error: {e}")
        return SkillIR(contract_id=None, version=1, params={}, confidence=0.0)
