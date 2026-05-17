# contract.dashboard_filters

## Layer Type
contract

## Hierarchy
contract → component → pattern → layout → skill

## Purpose
Semantic contract for dashboard-level filter state.

## Fields
- date_range: { start: datetime, end: datetime }
- metrics: string[] — selected metric identifiers
- dimensions: { key: string, values: string[] }[]
- comparison_period: string — previous_period | year_ago | custom

## Common Usage
- dashboard filter synchronization
- cross-component filter state
- URL-persisted filter state
- analytics workspace controls

## Related
- component.FilterBar
- pattern.filter_panel
- layout.SidebarAnalyticsLayout

## Keywords
filter, contract, dashboard state, date range, dimension, selection, analytics controls
