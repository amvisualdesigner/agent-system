# skill.dashboard.executive_summary

## Layer Type
skill

## Hierarchy
component → pattern → layout → skill

## Intent Signature
Use when building a high-level executive health dashboard with headline KPIs, revenue overview, and key business alerts.

## Purpose
Semantic skill for executive-level business health dashboard.

## Contains
- headline KPI row (revenue, growth, retention, active users)
- revenue trend with period comparison
- retention and churn snapshot
- key alerts or notable changes section

## Composition
- layout.ExecutiveOverview
- pattern.executive_summary

## Related
- component.RevenuePanel
- component.RetentionCard
- component.GrowthWidget
- component.KpiCard
- contract.kpi_metric
- contract.revenue_series

## Keywords
executive, summary, dashboard, health, overview, kpi, revenue, retention, c-level, strategic
