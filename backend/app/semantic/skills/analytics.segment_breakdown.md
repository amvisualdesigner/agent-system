# skill.analytics.segment_breakdown

## Layer Type
skill

## Hierarchy
component → pattern → layout → skill

## Intent Signature
Use when breaking down metrics by customer segment, product category, region, or other dimension with comparison and contribution analysis.

## Purpose
Dimensional segment analysis skill for breakdown comparisons.

## Contains
- segment dimension selector
- metric-per-segment comparison table
- contribution percentage visualization
- period comparison per segment
- segment trend sparklines

## Composition
- component.SegmentBreakdown
- component.AnalyticsTable
- contract.dashboard_filters
- pattern.analytics_workspace

## Related
- skill.dashboard.analytics_workspace
- component.FilterBar
- layout.MetricsWorkspace
- contract.kpi_metric

## Keywords
segment, breakdown, dimension, analysis, comparison, contribution, category, regional, cohort
