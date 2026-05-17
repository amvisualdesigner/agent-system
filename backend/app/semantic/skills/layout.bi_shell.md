# skill.layout.bi_shell

## Layer Type
skill

## Hierarchy
component → pattern → layout → skill

## Intent Signature
Use when creating a full BI application shell with navigation, sidebar filters, content area, and multi-page dashboard routing.

## Purpose
Full BI application layout skill with navigation and persistent chrome.

## Contains
- sidebar with navigation menu
- top header with global controls
- main content area for dashboard content
- persistent filter bar
- user and settings menu

## Composition
- layout.SidebarAnalyticsLayout
- pattern.filter_panel
- component.FilterBar

## Related
- layout.ExecutiveOverview
- layout.MetricsWorkspace
- skill.dashboard.analytics_workspace
- pattern.dashboard_page

## Keywords
bi, shell, application, layout, navigation, sidebar, chrome, container, BI platform
