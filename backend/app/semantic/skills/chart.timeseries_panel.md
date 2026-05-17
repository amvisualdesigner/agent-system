# skill.chart.timeseries_panel

## Layer Type
skill

## Hierarchy
component → pattern → layout → skill

## Intent Signature
Use when embedding a standalone timeseries chart panel with metric selection, date range, and trend comparison in any analytics view.

## Purpose
Standalone timeseries chart skill for embedding in dashboards.

## Contains
- line or area chart
- metric selector dropdown
- date range controls
- period comparison overlay
- trend analysis annotations

## Composition
- component.TimeseriesChart
- pattern.timeseries_section
- contract.timeseries_metric

## Related
- skill.dashboard.timeseries_panel
- skill.chart.revenue_trend
- component.FilterBar
- layout.AnalyticsGrid

## Keywords
chart, timeseries, panel, metric, trend, visualization, embeddable, analytics chart
