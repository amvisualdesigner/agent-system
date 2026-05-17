# contract.timeseries_metric

## Layer Type
contract

## Hierarchy
contract → component → pattern → layout → skill

## Purpose
Semantic contract for timeseries data points.

## Fields
- timestamp: datetime — observation time (ISO 8601)
- value: number — metric value at timestamp
- metric_name: string — identifier for the metric
- unit: string (optional) — measurement unit

## Common Usage
- timeseries charts and panels
- trend analysis views
- period-over-period comparisons
- revenue and growth tracking

## Compatible With
- component.TimeseriesChart
- skill.chart.timeseries_panel
- pattern.timeseries_section
- contract.revenue_series

## Keywords
timeseries, datapoint, temporal, interval, metric stream, time-based data, analytics
