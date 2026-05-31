#!/usr/bin/env python3
"""Generate capability_catalog.json from SKILL_CONTRACTS + alias tables.

Usage:
    python scripts/generate_capability_catalog.py              # generate
    python scripts/generate_capability_catalog.py --check      # verify up-to-date
"""

from __future__ import annotations

import json
import os
import sys
import argparse


CATALOG_PATH = os.path.join(
    os.path.dirname(__file__), "..",
    "backend/app/catalog/capability_catalog.json",
)


def _load_aliases() -> dict[str, str]:
    """Load FILENAME_ALIASES from aliases module."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
    from app.engine.aliases import FILENAME_ALIASES
    return dict(FILENAME_ALIASES)


def _load_graphir_types() -> dict[str, str]:
    """Load _CAPABILITY_TO_GRAPHIR_TYPE from intent module."""
    from app.graphir.intent import _CAPABILITY_TO_GRAPHIR_TYPE
    return dict(_CAPABILITY_TO_GRAPHIR_TYPE)


def _load_object_keywords() -> dict[str, str]:
    """Load _OBJECT_KEYWORDS from semantic_frame module."""
    from app.graphir.semantic_frame import _OBJECT_KEYWORDS
    return dict(_OBJECT_KEYWORDS)


def _load_contracts():
    """Load SKILL_CONTRACTS from skill_registry module."""
    from app.contracts.skill_registry import SKILL_CONTRACTS, SkillContract

    contracts: dict[str, dict] = {}
    for (cid, ver), contract in SKILL_CONTRACTS.items():
        if cid == "noop":
            continue
        contracts.setdefault(cid, {
            "label": _contract_label(cid),
            "description": _contract_description(cid),
            "capabilities": [],
            "utterance_examples": _utterance_examples(cid),
        })

        caps = contract.ast_template.get("capabilities", {})
        for template_key, cap_id in caps.items():
            contracts[cid]["capabilities"].append({
                "id": cap_id,
                "label": _capability_label(cap_id),
                "graphir_type": template_key,
                "synonyms": _synonyms_for(cap_id),
                "filename_aliases": _filename_aliases_for(cap_id),
                "verbs": _verbs_for(cap_id),
                "params_schema_ref": _params_schema_ref(cap_id),
            })

    return contracts


def _contract_label(contract_id: str) -> str:
    labels = {
        "dashboard.sales_overview": "Sales dashboard",
        "analytics.table": "Data table",
        "analytics.filter": "Filter panel",
        "analytics.chart_bar": "Bar chart",
        "analytics.metric_card": "Metric card",
        "embed.external": "Embedded content",
        "interaction.search": "Search",
        "interaction.form": "Form",
        "data.export": "Data export",
        "data.drilldown": "Drilldown",
    }
    return labels.get(contract_id, contract_id)


def _contract_description(contract_id: str) -> str:
    descriptions = {
        "dashboard.sales_overview": "KPI row, trend chart, executive layout",
        "analytics.table": "Columnar data table with metrics",
        "analytics.filter": "Filter panel for data exploration",
        "analytics.chart_bar": "Bar chart visualization",
        "analytics.metric_card": "Single metric display card",
        "embed.external": "Embedded external content iframe",
        "interaction.search": "Search bar component",
        "interaction.form": "Input form component",
        "data.export": "Data export button",
        "data.drilldown": "Drilldown navigation component",
    }
    return descriptions.get(contract_id, "")


def _capability_label(cap_id: str) -> str:
    labels = {
        "presentation.kpi_row": "KPI row",
        "presentation.timeseries": "Trend chart",
        "presentation.table": "Data table",
        "presentation.filter_panel": "Filter panel",
        "presentation.embed": "Embedded content",
        "presentation.chart.bar": "Bar chart",
        "presentation.metric_card": "Metric card",
        "layout.page": "Dashboard page",
        "layout.grid": "Layout grid",
        "layout.container": "Layout container",
        "domain.sales": "Sales domain",
        "domain.analytics": "Analytics domain",
        "interaction.search": "Search",
        "interaction.form": "Form",
        "data.export": "Export",
        "data.drilldown": "Drilldown",
    }
    return labels.get(cap_id, cap_id)


def _synonyms_for(cap_id: str) -> list[str]:
    synonyms_map: dict[str, list[str]] = {
        "presentation.kpi_row": ["kpi", "kpi row", "metrics row", "indicators", "metric", "metrics"],
        "presentation.timeseries": ["timeseries", "line chart", "trend", "line", "linechart"],
        "presentation.table": ["table", "data table", "tabular", "analytics table", "columns"],
        "presentation.filter_panel": ["filter", "facet", "filters", "filter panel"],
        "presentation.embed": ["embed", "embedded", "iframe"],
        "presentation.chart.bar": ["bar chart", "barchart", "bar graph", "chart"],
        "presentation.metric_card": ["metric card", "card", "metric"],
        "layout.page": ["page", "dashboard", "overview", "sales overview"],
        "layout.grid": ["grid", "layout grid"],
        "layout.container": ["container", "panel", "section"],
        "domain.sales": ["sales", "sales data"],
        "domain.analytics": ["analytics", "analytics data"],
        "interaction.search": ["search", "search bar"],
        "interaction.form": ["form", "input form"],
        "data.export": ["export", "download"],
        "data.drilldown": ["drilldown", "drill down"],
    }
    return synonyms_map.get(cap_id, [])


def _filename_aliases_for(cap_id: str) -> list[str]:
    aliases_module = _load_aliases()
    # Collect all aliases from FILENAME_ALIASES that map to this cap_id
    return sorted(
        alias for alias, cap in aliases_module.items()
        if cap == cap_id
    )


def _verbs_for(cap_id: str) -> dict[str, list[str]]:
    verbs_map: dict[str, dict[str, list[str]]] = {
        "presentation.kpi_row": {
            "remove": ["remove", "delete", "hide", "delete kpi", "remove kpi"],
            "modify": ["update", "change", "set", "edit", "modify", "update metrics"],
        },
        "presentation.timeseries": {
            "remove": ["remove", "delete", "hide"],
            "modify": ["update", "change", "set", "modify"],
        },
        "presentation.table": {
            "remove": ["remove", "delete", "hide"],
            "modify": ["update", "change", "modify"],
        },
    }
    return verbs_map.get(cap_id, {
        "remove": ["remove", "delete"],
        "modify": ["update", "change", "modify", "edit"],
    })


def _params_schema_ref(cap_id: str) -> str | None:
    refs = {
        "presentation.kpi_row": "metrics",
        "presentation.timeseries": "timeseries_metric",
        "presentation.table": "columns",
        "presentation.chart.bar": "categories",
    }
    return refs.get(cap_id)


def _utterance_examples(contract_id: str) -> list[dict]:
    examples_map: dict[str, list[dict]] = {
        "dashboard.sales_overview": [
            {
                "text": "Remove the KPI row from the sales dashboard",
                "intent": {
                    "actions": [
                        {"verb": "remove", "target_capability": "presentation.kpi_row"},
                    ],
                },
            },
            {
                "text": "Update KPI metrics to revenue and growth",
                "intent": {
                    "actions": [
                        {"verb": "modify", "target_capability": "presentation.kpi_row",
                         "params": {"metrics": ["revenue", "growth"]}},
                    ],
                },
            },
            {
                "text": "Remove the line chart from the sales dashboard",
                "intent": {
                    "actions": [
                        {"verb": "remove", "target_capability": "presentation.timeseries"},
                    ],
                },
            },
        ],
        "analytics.table": [
            {
                "text": "Show revenue and growth columns in the table",
                "intent": {
                    "actions": [
                        {"verb": "modify", "target_capability": "presentation.table",
                         "params": {"columns": ["revenue", "growth"]}},
                    ],
                },
            },
        ],
    }
    return examples_map.get(contract_id, [])


def build_catalog() -> dict:
    """Build the full capability catalog dict."""
    return {
        "version": 1,
        "contracts": _load_contracts(),
    }


def write_catalog(catalog: dict, path: str = CATALOG_PATH) -> str:
    """Write catalog JSON to disk. Returns the path written."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def load_catalog(path: str = CATALOG_PATH) -> dict | None:
    """Load existing catalog from disk."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def check_catalog(path: str = CATALOG_PATH) -> bool:
    """Check if generated catalog matches current on disk. Returns True if up-to-date."""
    generated = build_catalog()
    existing = load_catalog(path)
    if existing is None:
        print(f"[check] Catalog not found at {path}", file=sys.stderr)
        return False
    if generated != existing:
        print("[check] Catalog is out of date — regenerate with generate_capability_catalog.py", file=sys.stderr)
        return False
    print("[check] Catalog is up to date")
    return True


def main():
    parser = argparse.ArgumentParser(description="Generate capability_catalog.json")
    parser.add_argument("--check", action="store_true", help="Check if catalog is up-to-date")
    args = parser.parse_args()

    # Ensure backend is importable
    backend_path = os.path.join(os.path.dirname(__file__), "..", "backend")
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)

    if args.check:
        sys.exit(0 if check_catalog() else 1)

    catalog = build_catalog()
    written = write_catalog(catalog)
    print(f"[generate] Catalog written to {written}")
    print(f"[generate] {len(catalog['contracts'])} contracts")
    total_caps = sum(len(c["capabilities"]) for c in catalog["contracts"].values())
    print(f"[generate] {total_caps} capabilities total")


if __name__ == "__main__":
    main()
