"""Aliases centralizados — fuente única de filename→capability y object keywords.

Reglas:
  - FILENAME_ALIASES alimenta _build_name_map() para que StructuralIndex
    detecte archivos reales del worktree (3A)
  - OBJECT_KEYWORDS_EXTRA completa _OBJECT_KEYWORDS para action binding (3B)
  - El generador de capability_catalog.json lee ambas tablas
"""

FILENAME_ALIASES: dict[str, str] = {
    # agent-test-repo: LineChart.tsx → presentation.timeseries
    "linechart": "presentation.timeseries",
    "line-chart": "presentation.timeseries",
    # agent-test-repo: SalesOverviewPage.tsx → layout.page
    "salesoverviewpage": "layout.page",
    "sales-overview-page": "layout.page",
    # agent-test-repo: BarChart.tsx → presentation.chart.bar
    "barchart": "presentation.chart.bar",
    "bar-chart": "presentation.chart.bar",
    # agent-test-repo: DataTable.tsx → presentation.table
    "datatable": "presentation.table",
    "data-table": "presentation.table",
    # AnalyticsTable.tsx (renderer path) → presentation.table
    "analytictable": "presentation.table",
    "analytics-table": "presentation.table",
}

OBJECT_KEYWORDS_EXTRA: dict[str, str] = {
    # Missing from _OBJECT_KEYWORDS — needed for "remove line chart"
    "line": "timeseries",
    "line chart": "timeseries",
    "linechart": "timeseries",
    # Dashboard overview page
    "sales overview": "page",
    "overview": "page",
}
