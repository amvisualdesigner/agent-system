# component.KpiCard

## Layer Type
component

## Hierarchy
component → pattern → layout → skill

## Purpose
Display a single KPI metric with trend indication.

## Displays
- metric label
- metric value
- trend direction
- delta percentage

## Semantic Tags
analytics, dashboard, metric, kpi, business intelligence

## Props
- title: string
- value: number
- delta: number (optional)

## Composition Rules
- usable_inside: pattern.kpi_section, layout.AnalyticsGrid
- typical_context: executive dashboards, analytics workspaces, SaaS reporting

## Related
- pattern.kpi_section
- layout.AnalyticsGrid
- contract.kpi_metric
- component.TrendIndicator

## Keywords
kpi, metrics, analytics, dashboard, business intelligence, trend, revenue
