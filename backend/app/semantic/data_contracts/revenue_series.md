# contract.revenue_series

## Layer Type
contract

## Hierarchy
contract → component → pattern → layout → skill

## Purpose
Semantic contract for revenue timeseries data.

## Fields
- period: string — monthly | quarterly | yearly
- revenue: number — total revenue in period
- forecast: number (optional) — projected revenue
- previous_period: number — revenue in comparable prior period
- growth_rate: number — period-over-period percentage

## Common Usage
- revenue dashboards and panels
- financial reporting views
- sales analytics interfaces
- executive summaries

## Related
- component.RevenuePanel
- skill.chart.revenue_trend
- contract.timeseries_metric
- pattern.executive_summary

## Keywords
revenue, financial, series, contract, sales data, income, monetary, growth trend
