# skill.dashboard.analytics_workspace

## Layer Type
skill

## Hierarchy
component → pattern → layout → skill

## Intent Signature
Use when building a full analytics workspace for deep data exploration with flexible metric arrangement, filtering, and detail views.

## Purpose
Semantic skill for a complete analytics exploration workspace.

## Contains
- configurable metric grid with drag-and-drop panels
- advanced filter panel with multi-dimension support
- detail table with pagination and export
- chart and table toggle views

## Composition
- layout.MetricsWorkspace
- pattern.analytics_workspace
- component.AnalyticsTable
- component.FilterBar

## Related
- component.SegmentBreakdown
- contract.dashboard_filters
- layout.SidebarAnalyticsLayout
- pattern.filter_panel

## Keywords
workspace, analytics, exploration, flexible, filters, deep dive, data analysis, BI tool
