"""Catalog loader — loads capability_catalog.json at runtime.

Regla: el catálogo es vocabulario (lenguaje), no lee disco ni decide lifecycle.
Lo consumen: IntentInterpreter (LLM), UI (labels/chips), y tests.
"""

from __future__ import annotations

import json
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)

_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "capability_catalog.json")
_catalog_cache: dict | None = None


def load_catalog(path: str | None = None) -> dict:
    """Load capability catalog JSON.

    Cached after first load. Returns dict with version + contracts.
    """
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache

    p = path or _CATALOG_PATH
    if not os.path.isfile(p):
        logger.warning("Catalog file not found at %s", p)
        _catalog_cache = {"version": 0, "contracts": {}}
        return _catalog_cache

    with open(p) as f:
        _catalog_cache = json.load(f)
    return _catalog_cache


def get_contract_catalog(contract_id: str) -> dict | None:
    """Get a single contract's catalog entry."""
    catalog = load_catalog()
    return catalog.get("contracts", {}).get(contract_id)


def list_contract_ids() -> list[str]:
    """List all contract IDs in the catalog."""
    catalog = load_catalog()
    return list(catalog.get("contracts", {}).keys())


def clear_cache() -> None:
    """Clear catalog cache (for testing)."""
    global _catalog_cache
    _catalog_cache = None
