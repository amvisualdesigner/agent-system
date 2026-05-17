# contract.kpi_metric

## Layer Type
contract

## Hierarchy
contract → component → pattern → layout → skill

## Purpose
Semantic contract for KPI metric data structures.

## Fields
- label: string — human-readable metric name
- value: number — current metric value
- delta: number — change from previous period
- delta_percentage: number — relative change
- trend: string — up | down | flat
- timestamp: datetime — observation time

## Common Usage
- KPI cards and metric badges
- dashboard summary rows
- executive analytics views
- period comparison displays

## Related
- component.KpiCard
- skill.dashboard.kpi_row
- pattern.kpi_section
- component.TrendIndicator

## Keywords
kpi, metric, contract, data shape, key performance indicator, measurement, analytics data
