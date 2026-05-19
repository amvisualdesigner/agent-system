"""ReactBackend — generates React/TSX files from GraphIR + GraphIRLayout.

Stateless, no caching, no graph interpretation, no layout derivation.

Component generation uses a dispatch dict (ComponentGenerator) registered
by GraphIRNode.type. This avoids if/switch chains while allowing
type-specific output logic — which IS the renderer's job per the
architecture ("Maps GraphIRNode.type → framework component implementation").

Layout wrappers are derived from LayoutConstraint only (never from EdgeRole).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from app.graphir.models import GraphIR, GraphIRLayout, GraphIRNode, LayoutConstraint
from app.graphir.backends.base import BackendRenderer, BackendConfig
from app.graphir.models import FileOp

ComponentGenerator = Callable[
    [GraphIRNode, list[LayoutConstraint], "ReactBackend", BackendConfig],
    str,
]


class ReactBackend(BackendRenderer):
    """React/TSX backend — generates .tsx files from GraphIR.

    Uses registered ComponentGenerator functions for each node type.
    Register new generators with ReactBackend.register(type_name, generator).

    Built-in generators cover: Page, KpiRow, Timeseries, AnalyticsTable.
    """

    _generators: dict[str, ComponentGenerator] = {}

    @classmethod
    def register(cls, type_name: str, generator: ComponentGenerator) -> None:
        cls._generators[type_name] = generator

    @classmethod
    def registered_types(cls) -> set[str]:
        return set(cls._generators.keys())

    def framework_name(self) -> str:
        return "react"

    def render(
        self,
        graph: GraphIR,
        layout: GraphIRLayout,
        config: BackendConfig,
    ) -> list[FileOp]:
        fileops: list[FileOp] = []

        children_by_source: dict[str, list[str]] = {}
        for edge in graph.edges:
            children_by_source.setdefault(edge.source, []).append(edge.target)

        for node in graph.nodes.values():
            generator = self._generators.get(node.type)
            if generator is None:
                continue

            node_constraints = layout.constraints.get(node.id, [])
            content = generator(node, node_constraints, self, config)
            file_path = self._resolve_file_path(node, config)

            # Inject child composition and imports for container nodes
            child_ids = children_by_source.get(node.id, [])
            if child_ids:
                composition = self._render_composition(child_ids, graph, config)
                content = self._inject_composition(content, composition)

            fileops.append(FileOp(action="create", path=file_path, content=content))

        return fileops

    def _resolve_file_path(self, node: GraphIRNode, config: BackendConfig) -> str:
        if node.type in config.path_map:
            override = config.path_map[node.type]
            base = config.output_base_path.rstrip("/")
            return os.path.normpath(f"{base}/{override}")
        base = config.output_base_path.rstrip("/")
        return os.path.normpath(f"{base}/{node.type}{config.file_extension}")

    def _render_composition(
        self,
        child_ids: list[str],
        graph: GraphIR,
        config: BackendConfig,
    ) -> str:
        parts: list[str] = []
        for cid in child_ids:
            child = graph.nodes.get(cid)
            if child is None:
                continue
            props_str = self._render_props_jsx(child.data)
            parts.append(f"<{child.type} {props_str} />" if props_str else f"<{child.type} />")
        return "\n".join(parts)

    @staticmethod
    def _render_props_jsx(data: dict) -> str:
        if not data:
            return ""
        return " ".join(f"{k}={json.dumps(v)}" for k, v in data.items())

    @staticmethod
    def _inject_composition(content: str, composition: str) -> str:
        if not composition:
            return content
        return content.replace("__COMPOSITION__", composition)

    @staticmethod
    def _layout_to_wrapper(constraints: list[LayoutConstraint]) -> tuple[str, str]:
        if LayoutConstraint.ROW in constraints:
            return ("<AnalyticsGrid>", "</AnalyticsGrid>")
        if LayoutConstraint.COLUMN in constraints:
            return ('<div className="column-layout">', "</div>")
        if LayoutConstraint.STACK in constraints:
            return ('<div className="stack-layout">', "</div>")
        return ("<div>", "</div>")


# ── Built-in Component Generators ──────────────────────────────────────


def _generate_page(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    open_tag, close_tag = ReactBackend._layout_to_wrapper(constraints)
    lines = [
        "import React from 'react';",
        "",
        f"export const {node.type}: React.FC = () => {{",
        "  return (",
        f"    {open_tag}",
        "      __COMPOSITION__",
        f"    {close_tag}",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


def _generate_kpi_row(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    metrics = node.data.get("metrics", [])
    cards = "\n".join(
        f'      <Card key="{m}">'
        f'\n        <div className="kpi-card">'
        f'\n          <span className="kpi-label">{m}</span>'
        f"\n        </div>"
        f"\n      </Card>"
        for m in metrics
    )

    lines = [
        "import React from 'react';",
        "import { Card } from '@/components/ui/Card';",
        "",
        f"interface {node.type}Props {{",
        "  metrics: string[];",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ metrics }}) => {{",
        "  return (",
        '    <div className="kpi-row">',
        cards,
        "    </div>",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


def _generate_timeseries(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    metric = node.data.get("metric", "—")

    lines = [
        "import React from 'react';",
        "import { Card } from '@/components/ui/Card';",
        "",
        f"interface {node.type}Props {{",
        "  metric: string;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ metric }}) => {{",
        "  return (",
        "    <Card>",
        '      <div className="timeseries-chart">',
        f"        <h3>{metric} over time</h3>",
        "      </div>",
        "    </Card>",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


def _generate_analytics_table(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    columns = node.data.get("columns", [])
    thead = "".join(f"<th>{c}</th>" for c in columns)

    table_data = node.data.get("table_data", [])
    if table_data:
        rows = []
        for row in table_data:
            if isinstance(row, dict):
                cells = "".join(f"<td>{json.dumps(row.get(c, ''))}</td>" for c in columns)
            elif isinstance(row, (list, tuple)):
                cells = "".join(f"<td>{json.dumps(cell)}</td>" for cell in row)
            else:
                cells = ""
            rows.append(f"<tr>{cells}</tr>")
    else:
        rows = ["<tr>" + "".join("<td>—</td>" for _ in columns) + "</tr>"] * 3
    tbody = "<tbody>\n" + "\n".join(rows) + "\n</tbody>"

    lines = [
        "import React from 'react';",
        "import { Card } from '@/components/ui/Card';",
        "",
        f"interface {node.type}Props {{",
        "  columns: string[];",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ columns }}) => {{",
        "  return (",
        "    <Card>",
        '      <table className="analytics-table">',
        "        <thead>",
        "          <tr>",
        f"            {thead}",
        "          </tr>",
        "        </thead>",
        f"        {tbody}",
        "      </table>",
        "    </Card>",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


# ── Register built-in generators ───────────────────────────────────────

ReactBackend.register("Page", _generate_page)
ReactBackend.register("KpiRow", _generate_kpi_row)
ReactBackend.register("Timeseries", _generate_timeseries)
ReactBackend.register("AnalyticsTable", _generate_analytics_table)
