# skill.dashboard.kpi_row

## Layer Type
skill

## Hierarchy
component → pattern → layout → skill

## Intent Signature
Use when displaying a horizontal row of KPI metric cards with trend indicators, typically at the top of a dashboard or analytics page.

## Purpose
Semantic skill for composing a KPI metric summary row.

## Contains
- horizontal row of KPI cards
- trend indicators per metric
- configurable metric selection
- responsive column adaptation

## Composition
- component.KpiCard (multiple instances)
- component.TrendIndicator (per card)
- pattern.kpi_section

## Related
- contract.kpi_metric
- layout.AnalyticsGrid
- skill.dashboard.sales_overview
- pattern.dashboard_page

## Keywords
kpi, row, metrics, summary, key indicators, dashboard top, metric row, analytics header
