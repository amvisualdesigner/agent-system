# skill.dashboard.timeseries_panel

## Layer Type
skill

## Hierarchy
component → pattern → layout → skill

## Intent Signature
Use when visualizing a metric over time with date range controls, trend overlay, and period comparison in an analytics dashboard.

## Purpose
Reusable analytics chart panel skill for timeseries visualization.

## Contains
- timeseries chart with configurable metric
- date range selector
- trend line overlay
- loading and empty states

## Composition
- component.TimeseriesChart
- component.FilterBar (date range)
- pattern.timeseries_section

## Related
- contract.timeseries_metric
- skill.chart.revenue_trend
- layout.AnalyticsGrid
- pattern.filter_panel

## Keywords
timeseries, chart, panel, analytics, trend, visualization, date range, metric timeline
