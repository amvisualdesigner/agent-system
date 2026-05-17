# skill.dashboard.sales_overview

## Layer Type
skill

## Hierarchy
component → pattern → layout → skill

## Intent Signature
Use when building a multi-metric SaaS analytics dashboard with KPI row, revenue timeseries, retention indicators, and executive metrics.

## Purpose
Semantic skill representing a complete sales analytics dashboard composition.

## Contains
- KPI row with revenue, growth, and retention metrics
- revenue timeseries chart
- retention indicators and cohort view
- executive summary section

## Composition
### Layout
- layout.AnalyticsGrid

### Sections
1. KPI row (top) — revenue, growth, retention, active users
2. Revenue timeseries (full width) — monthly trend
3. Retention and growth cards (2-column)
4. Optional breakdown table (bottom)

## Related
- component.KpiCard
- component.TimeseriesChart
- component.RevenuePanel
- component.RetentionCard
- layout.AnalyticsGrid
- pattern.dashboard_page
- contract.revenue_series
- contract.kpi_metric

## Keywords
dashboard, analytics, sales, revenue, kpi, timeseries, metrics, business intelligence, executive, retention
