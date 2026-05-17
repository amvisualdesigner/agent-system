"""Deterministic example retrieval for the renderer.

Maps contract_id → domain → ExampleContext.
No embeddings, no ML, no fuzzy matching.

Rules:
    - "dashboard.sales_overview" → "dashboard" domain
    - Fallback → empty ExampleContext
"""

import logging

from app.examples.models import ExampleContext
from app.examples.catalog_loader import get_catalog

logger = logging.getLogger(__name__)

_CONTRACT_DOMAIN_MAP: dict[str, str] = {
    "dashboard.sales_overview": "dashboard",
    "analytics.table": "tables",
}


def retrieve_examples(contract_id: str) -> ExampleContext:
    """Retrieve example context for a given contract_id.

    Args:
        contract_id: The contract_id from SkillIR (e.g. "dashboard.sales_overview")

    Returns:
        ExampleContext with imports, components, layouts, composition.
        Returns empty context if no catalog matches.
    """
    domain = _CONTRACT_DOMAIN_MAP.get(contract_id)
    if domain is None:
        logger.info("no example domain for contract_id=%s", contract_id)
        return ExampleContext.empty()

    ctx = get_catalog(domain)
    if not ctx.imports and not ctx.components:
        logger.info("empty catalog for domain=%s contract_id=%s", domain, contract_id)
        return ExampleContext.empty()

    logger.info(
        "examples_retrieved contract_id=%s domain=%s components=%d imports=%d",
        contract_id, domain, len(ctx.components), len(ctx.imports),
    )
    return ctx
