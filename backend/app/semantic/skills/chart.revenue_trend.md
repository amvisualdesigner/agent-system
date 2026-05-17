# skill.chart.revenue_trend

## Layer Type
skill

## Hierarchy
component → pattern → layout → skill

## Intent Signature
Use when displaying revenue trend with period-over-period comparison, forecast overlay, and growth rate in a financial analytics context.

## Purpose
Revenue-focused timeseries chart skill with financial semantics.

## Contains
- revenue line chart with area fill
- period-over-period comparison (MoM, YoY)
- forecast projection overlay
- growth rate annotations
- revenue target vs actual markers

## Composition
- component.RevenuePanel
- component.TimeseriesChart
- contract.revenue_series
- pattern.timeseries_section

## Related
- skill.dashboard.sales_overview
- skill.chart.timeseries_panel
- contract.revenue_series
- layout.ExecutiveOverview

## Keywords
revenue, trend, financial, chart, growth, forecast, comparison, sales analytics, monetary
