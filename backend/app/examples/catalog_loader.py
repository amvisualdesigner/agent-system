"""Load example catalog JSONs at runtime.

The catalogs are built OFFLINE by scripts/build_examples_catalog.py.
The runtime ONLY reads pre-built JSON — never parses .tsx files.
"""

import hashlib
import json
import logging
from pathlib import Path

from app.examples.models import ExampleContext

logger = logging.getLogger(__name__)

RENDERER_SCHEMA_VERSION = "2026-05"
_CATALOG_DIR = Path(__file__).resolve().parent / "catalog"
_cache: dict[str, ExampleContext] = {}


# ── metric stub ────────────────────────────────────────────────────
# Placeholder for future metrics backend. Replace with real client
# when observability infra is wired in.
class _Metrics:
    @staticmethod
    def increment(name: str, tags: dict | None = None) -> None:
        logger.debug("metric %s tags=%s", name, tags or {})


metrics = _Metrics()


def _compute_imports_hash(imports: list[str]) -> str:
    if not imports:
        return ""
    blob = "\n".join(sorted(imports)).encode()
    return hashlib.sha1(blob).hexdigest()[:8]


def _check_schema_version(data: dict, domain: str) -> bool:
    cat_ver = data.get("renderer_schema_version")
    if cat_ver is None:
        logger.warning("missing_schema_version domain=%s", domain)
        metrics.increment("catalog_schema_missing", tags={"domain": domain})
        return False
    if cat_ver != RENDERER_SCHEMA_VERSION:
        logger.warning(
            "schema_mismatch domain=%s catalog=%s renderer=%s",
            domain, cat_ver, RENDERER_SCHEMA_VERSION,
        )
        metrics.increment("catalog_schema_mismatch", tags={"domain": domain})
        return False
    return True


def _load_catalog(domain: str) -> ExampleContext | None:
    path = _CATALOG_DIR / f"{domain}.json"
    if not path.exists():
        logger.debug("catalog not found for domain=%s path=%s", domain, path)
        return None
    try:
        data = json.loads(path.read_text())

        imports: list[str] = data.get("imports", [])
        imports_hash = _compute_imports_hash(imports)

        schema_ok = _check_schema_version(data, domain)

        logger.info(
            "catalog_loaded domain=%s catalog_version=%s schema_version=%s "
            "renderer_schema=%s imports_hash=%s components=%d schema_ok=%s",
            domain,
            data.get("version"),
            data.get("renderer_schema_version"),
            RENDERER_SCHEMA_VERSION,
            imports_hash,
            len(data.get("components", [])),
            schema_ok,
        )

        return ExampleContext(
            imports=imports,
            components=data.get("components", []),
            layouts=data.get("layouts", []),
            composition=[tuple(e) for e in data.get("composition", [])],
        )
    except Exception as e:
        logger.warning("failed to load catalog domain=%s: %s", domain, e)
        return None


def get_catalog(domain: str) -> ExampleContext:
    """Load a catalog by domain, with caching."""
    if domain not in _cache:
        ctx = _load_catalog(domain)
        if ctx is None:
            ctx = ExampleContext.empty()
        _cache[domain] = ctx
    return _cache[domain]


def clear_cache() -> None:
    _cache.clear()
