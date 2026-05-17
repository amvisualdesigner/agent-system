# architecture.dashboard_conventions

## Layer Type
architecture

## Hierarchy
architecture → component → pattern → layout → skill

## Purpose
Standard conventions for BI dashboard design. Ensures consistency across analytics interfaces.

## Conventions
- Maximum 4 KPI cards per row
- Charts prefer full-width rows below KPIs
- Timeseries x-axis: always datetime with consistent intervals
- Colors: semantic green/red for positive/negative trends
- Empty states: show skeleton loading, never raw errors
- Data density: prefer summary metrics over raw tables in primary view

## Common Usage
- BI dashboard creation
- Analytics UI design
- Reporting interface standards

## Related
- architecture.ui_composition
- pattern.kpi_section
- contract.kpi_metric

## Keywords
dashboard conventions, BI standards, analytics rules, visualization guidelines, data display, KPI layout, chart placement
