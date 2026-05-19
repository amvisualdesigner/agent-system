from __future__ import annotations

from dataclasses import dataclass, field


PER_CONTRACT_THRESHOLDS: dict[str, float] = {
    "analytics.table": 0.4,
}


@dataclass
class SkillContract:
    contract_id: str
    version: int
    input_schema: dict
    ast_template: dict
    renderer: dict


SKILL_CONTRACTS: dict[tuple[str, int], SkillContract] = {
    ("noop", 1): SkillContract(
        contract_id="noop",
        version=1,
        input_schema={"type": "object", "properties": {}, "required": []},
        ast_template={"layout": None, "slots": []},
        renderer={},
    ),
    ("dashboard.sales_overview", 1): SkillContract(
        contract_id="dashboard.sales_overview",
        version=1,
        input_schema={
            "type": "object",
            "required": ["metrics"],
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["revenue", "growth", "retention", "churn"]},
                    "minItems": 1,
                    "maxItems": 4,
                },
                "timeseries_metric": {
                    "type": "string",
                    "enum": ["revenue", "growth", "retention"],
                    "default": "revenue",
                },
            },
        },
        ast_template={
            "layout": "AnalyticsGrid",
            "slots": [
                {"type": "KpiRow", "props": {"metrics": "metrics"}},
                {"type": "Timeseries", "props": {"metric": "timeseries_metric"}},
            ],
            "capabilities": {
                "KpiRow": "display.kpi_row",
                "Timeseries": "display.timeseries",
                "Page": "layout.page",
            },
        },
        renderer={
            "base_path": "src/pages/dashboard/",
            "files": [
                {"path": "Page.tsx", "template": "dashboard_page.j2"},
                {"path": "components/KpiRow.tsx", "template": "kpi_row.j2"},
                {"path": "components/Timeseries.tsx", "template": "timeseries.j2"},
            ],
        },
    ),
    ("analytics.table", 1): SkillContract(
        contract_id="analytics.table",
        version=1,
        input_schema={
            "type": "object",
            "required": ["columns"],
            "properties": {
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 8,
                },
            },
        },
        ast_template={
            "layout": None,
            "slots": [
                {"type": "AnalyticsTable", "props": {"columns": "columns", "table_data": "table_data"}},
            ],
            "capabilities": {
                "AnalyticsTable": "display.analytics_table",
            },
        },
        renderer={
            "base_path": "src/pages/analytics/",
            "files": [
                {"path": "AnalyticsTable.tsx", "template": "analytics_table.j2"},
            ],
        },
    ),
}


def get_contract(contract_id: str, version: int) -> SkillContract | None:
    return SKILL_CONTRACTS.get((contract_id, version))


def contract_exists(contract_id: str) -> bool:
    return any(cid == contract_id for (cid, _) in SKILL_CONTRACTS)


def list_contracts() -> list[tuple[str, int]]:
    return list(SKILL_CONTRACTS.keys())
