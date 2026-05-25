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
    capability_param_map: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self):
        """Precompile capability_param_map from ast_template.slots at init time.

        Each slot's props map capability_field → contract_param_name.
        Example:
          Slot: {"type": "Timeseries", "props": {"metric": "timeseries_metric"}}
          → capability_param_map["presentation.timeseries"] = {"metric": "timeseries_metric"}
        """
        if not self.capability_param_map:
            self.capability_param_map = _compile_capability_param_map(self)


def _compile_capability_param_map(contract: SkillContract) -> dict[str, dict[str, str]]:
    """Precompile: ast_template.slots → {capability: {cap_field: contract_param}}.

    This is computed once at contract load time, never at runtime.
    Structural completion reads this map — it does NOT parse slots.
    """
    mapping: dict[str, dict[str, str]] = {}
    caps = contract.ast_template.get("capabilities", {})
    for slot in contract.ast_template.get("slots", []):
        cap_name = caps.get(slot.get("type", ""))
        if cap_name:
            props = slot.get("props", {})
            mapping[cap_name] = dict(props)
    return mapping


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
                "KpiRow": "presentation.kpi_row",
                "Timeseries": "presentation.timeseries",
                "Page": "layout.page",
                "Domain": "domain.sales",
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
                "AnalyticsTable": "presentation.table",
            },
        },
        renderer={
            "base_path": "src/pages/analytics/",
            "files": [
                {"path": "AnalyticsTable.tsx", "template": "analytics_table.j2"},
            ],
        },
    ),
    ("analytics.filter", 1): SkillContract(
        contract_id="analytics.filter",
        version=1,
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "filters": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        ast_template={
            "layout": None,
            "slots": [
                {"type": "FilterPanel", "props": {"filters": "filters"}},
            ],
            "capabilities": {
                "FilterPanel": "presentation.filter_panel",
            },
        },
        renderer={
            "base_path": "src/pages/analytics/",
            "files": [
                {"path": "components/FilterPanel.tsx", "template": "filter_panel.j2"},
            ],
        },
    ),
    ("analytics.chart_bar", 1): SkillContract(
        contract_id="analytics.chart_bar",
        version=1,
        input_schema={
            "type": "object",
            "required": ["categories", "values"],
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "values": {
                    "type": "array",
                    "items": {"type": "number"},
                },
            },
        },
        ast_template={
            "layout": None,
            "slots": [
                {"type": "BarChart", "props": {"categories": "categories", "values": "values"}},
            ],
            "capabilities": {
                "BarChart": "presentation.chart.bar",
            },
        },
        renderer={
            "base_path": "src/pages/analytics/",
            "files": [
                {"path": "components/BarChart.tsx", "template": "bar_chart.j2"},
            ],
        },
    ),
    ("analytics.metric_card", 1): SkillContract(
        contract_id="analytics.metric_card",
        version=1,
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "value": {"type": ["string", "number"]},
                "label": {"type": "string"},
            },
        },
        ast_template={
            "layout": None,
            "slots": [
                {"type": "MetricCard", "props": {"value": "value", "label": "label"}},
            ],
            "capabilities": {
                "MetricCard": "presentation.metric_card",
            },
        },
        renderer={
            "base_path": "src/pages/analytics/",
            "files": [
                {"path": "components/MetricCard.tsx", "template": "metric_card.j2"},
            ],
        },
    ),
    ("embed.external", 1): SkillContract(
        contract_id="embed.external",
        version=1,
        input_schema={
            "type": "object",
            "required": ["src"],
            "properties": {
                "src": {"type": "string"},
                "title": {"type": "string"},
            },
        },
        ast_template={
            "layout": None,
            "slots": [
                {"type": "Embed", "props": {"src": "src", "title": "title"}},
            ],
            "capabilities": {
                "Embed": "presentation.embed",
            },
        },
        renderer={
            "base_path": "src/pages/embed/",
            "files": [
                {"path": "Embed.tsx", "template": "embed.j2"},
            ],
        },
    ),
    ("interaction.search", 1): SkillContract(
        contract_id="interaction.search",
        version=1,
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "placeholder": {"type": "string"},
            },
        },
        ast_template={
            "layout": None,
            "slots": [
                {"type": "SearchBar", "props": {"placeholder": "placeholder"}},
            ],
            "capabilities": {
                "SearchBar": "interaction.search",
            },
        },
        renderer={
            "base_path": "src/components/",
            "files": [
                {"path": "SearchBar.tsx", "template": "search_bar.j2"},
            ],
        },
    ),
    ("interaction.form", 1): SkillContract(
        contract_id="interaction.form",
        version=1,
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "key": {"type": "string"},
                            "type": {"type": "string"},
                        },
                    },
                },
            },
        },
        ast_template={
            "layout": None,
            "slots": [
                {"type": "Form", "props": {"fields": "fields"}},
            ],
            "capabilities": {
                "Form": "interaction.form",
            },
        },
        renderer={
            "base_path": "src/components/",
            "files": [
                {"path": "Form.tsx", "template": "form.j2"},
            ],
        },
    ),
    ("data.export", 1): SkillContract(
        contract_id="data.export",
        version=1,
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "format": {"type": "string"},
            },
        },
        ast_template={
            "layout": None,
            "slots": [
                {"type": "ExportButton", "props": {"format": "format"}},
            ],
            "capabilities": {
                "ExportButton": "data.export",
            },
        },
        renderer={
            "base_path": "src/components/",
            "files": [
                {"path": "ExportButton.tsx", "template": "export_button.j2"},
            ],
        },
    ),
    ("data.drilldown", 1): SkillContract(
        contract_id="data.drilldown",
        version=1,
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "label": {"type": "string"},
                "target": {"type": "string"},
            },
        },
        ast_template={
            "layout": None,
            "slots": [
                {"type": "Drilldown", "props": {"label": "label", "target": "target"}},
            ],
            "capabilities": {
                "Drilldown": "data.drilldown",
            },
        },
        renderer={
            "base_path": "src/components/",
            "files": [
                {"path": "Drilldown.tsx", "template": "drilldown.j2"},
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
