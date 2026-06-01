"""Aliases centralizados — filename→capability para StructuralIndex (3A).

Alimenta _build_name_map() para que StructuralIndex detecte archivos reales
del worktree. Object keywords viven en _OBJECT_KEYWORDS en semantic_frame.py.
"""

FILENAME_ALIASES: dict[str, str] = {
    "linechart": "presentation.timeseries",
    "line-chart": "presentation.timeseries",
    "salesoverviewpage": "layout.page",
    "sales-overview-page": "layout.page",
    "barchart": "presentation.chart.bar",
    "bar-chart": "presentation.chart.bar",
    "datatable": "presentation.table",
    "data-table": "presentation.table",
    "analytictable": "presentation.table",
    "analytics-table": "presentation.table",
}
