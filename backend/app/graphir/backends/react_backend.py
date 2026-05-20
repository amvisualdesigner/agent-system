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
        parts = []
        for k, v in data.items():
            if isinstance(v, str):
                parts.append(f'{k}="{v}"')
            elif isinstance(v, bool):
                parts.append(f"{k}={str(v).lower()}")
            elif v is None:
                continue
            else:
                parts.append(f"{k}={{{json.dumps(v)}}}")
        return " ".join(parts)

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
    map_block = (
        '{metrics.map((m) => (\n'
        '      <Card key={m}>\n'
        '        <div className="kpi-card">\n'
        '          <span className="kpi-label">{m}</span>\n'
        '        </div>\n'
        '      </Card>\n'
        '    ))}'
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
        map_block,
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
        '        <h3>{metric} over time</h3>',
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
    lines = [
        "import React from 'react';",
        "import { Card } from '@/components/ui/Card';",
        "",
        f"interface {node.type}Props {{",
        "  columns: string[];",
        "  rows: Record<string, any>[];",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ columns, rows = [] }}) => {{",
        "  return (",
        "    <Card>",
        '      <table className="analytics-table">',
        "        <thead>",
        "          <tr>",
        '            {columns.map(c => <th key={c}>{c}</th>)}',
        "          </tr>",
        "        </thead>",
        "        <tbody>",
        '          {rows.map((row, i) => (',
        "            <tr key={i}>",
        '              {columns.map(c => <td key={c}>{row[c] ?? "\u2014"}</td>)}',
        "            </tr>",
        "          ))}",
        "        </tbody>",
        "      </table>",
        "    </Card>",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


# ── Additional Component Generators ────────────────────────────────────


def _generate_filter_panel(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    lines = [
        "import React, { useState } from 'react';",
        "import { Card } from '@/components/ui/Card';",
        "",
        f"interface {node.type}Props {{",
        "  filters?: string[];",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ filters = [] }}) => {{",
        "  const [active, setActive] = useState<string[]>([]);",
        "  return (",
        "    <Card>",
        '      <div className="filter-panel">',
        '        {filters.map(f => (',
        '          <label key={f} className="filter-chip">',
        "            <input",
        '              type="checkbox"',
        '              checked={active.includes(f)}',
        "              onChange={() => setActive(prev =>",
        "                prev.includes(f)",
        "                  ? prev.filter(x => x !== f)",
        "                  : [...prev, f]",
        "              )}",
        "            />",
        "            {f}",
        "          </label>",
        "        ))}",
        "      </div>",
        "    </Card>",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


def _generate_bar_chart(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    lines = [
        "import React from 'react';",
        "import { Card } from '@/components/ui/Card';",
        "",
        f"interface {node.type}Props {{",
        "  categories?: string[];",
        "  values?: number[];",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ categories = [], values = [] }}) => {{",
        "  const max = Math.max(...values, 1);",
        "  return (",
        "    <Card>",
        '      <div className="bar-chart">',
        "        {categories.map((cat, i) => (",
        "          <div key={cat} className=\"bar-item\">",
        '            <span className="bar-label">{cat}</span>',
        '            <div className="bar-track">',
        '              <div className="bar-fill" style={{width: `${(values[i] ?? 0) / max * 100}%`}} />',
        "            </div>",
        "          </div>",
        "        ))}",
        "      </div>",
        "    </Card>",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


def _generate_metric_card(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    lines = [
        "import React from 'react';",
        "import { Card } from '@/components/ui/Card';",
        "",
        f"interface {node.type}Props {{",
        "  value?: string | number;",
        "  label?: string;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ value, label }}) => {{",
        "  return (",
        "    <Card>",
        '      <div className="metric-card">',
        '        {value !== undefined && <span className="metric-value">{value}</span>}',
        '        {label && <span className="metric-label">{label}</span>}',
        "      </div>",
        "    </Card>",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


def _generate_embed(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    lines = [
        "import React from 'react';",
        "",
        f"interface {node.type}Props {{",
        "  src?: string;",
        "  title?: string;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ src, title }}) => {{",
        "  return (",
        "    <div className=\"embed-container\">",
        '      {src ? <iframe src={src} title={title ?? "embedded content"} /> : null}',
        "    </div>",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


def _generate_search_bar(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    lines = [
        "import React, { useState } from 'react';",
        "",
        f"interface {node.type}Props {{",
        "  placeholder?: string;",
        "  onSearch?: (query: string) => void;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ placeholder = 'Search...', onSearch }}) => {{",
        "  const [query, setQuery] = useState('');",
        "  return (",
        '    <div className="search-bar">',
        "      <input",
        '        type="text"',
        "        value={query}",
        "        onChange={e => setQuery(e.target.value)}",
        '        placeholder={placeholder}',
        "      />",
        "      <button onClick={() => onSearch?.(query)}>Search</button>",
        "    </div>",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


def _generate_form(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    lines = [
        "import React, { useState } from 'react';",
        "import { Card } from '@/components/ui/Card';",
        "",
        f"interface {node.type}Props {{",
        "  fields?: { label: string; key: string; type: string }[];",
        "  onSubmit?: (data: Record<string, string>) => void;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ fields = [], onSubmit }}) => {{",
        "  const [data, setData] = useState<Record<string, string>>({});",
        "  return (",
        "    <Card>",
        '      <form className="form-component" onSubmit={e => { e.preventDefault(); onSubmit?.(data); }}>',
        "        {fields.map(f => (",
        "          <label key={f.key}>",
        "            {f.label}",
        "            <input",
        "              type={f.type ?? 'text'}",
        "              value={data[f.key] ?? ''}",
        "              onChange={e => setData(prev => ({...prev, [f.key]: e.target.value}))}",
        "            />",
        "          </label>",
        "        ))}",
        '        <button type="submit">Submit</button>',
        "      </form>",
        "    </Card>",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


def _generate_export_button(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    lines = [
        "import React from 'react';",
        "",
        f"interface {node.type}Props {{",
        "  format?: string;",
        "  onExport?: () => void;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ format = 'csv', onExport }}) => {{",
        "  return (",
        '    <div className="export-button">',
        "      <button onClick={onExport}>Export as {format.toUpperCase()}</button>",
        "    </div>",
        "  );",
        "};",
        "",
    ]
    return "\n".join(lines)


def _generate_drilldown(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    lines = [
        "import React from 'react';",
        "",
        f"interface {node.type}Props {{",
        "  label?: string;",
        "  target?: string;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ label = 'View details', target }}) => {{",
        "  return (",
        "    <a",
        '      className="drilldown-link"',
        "      href={target ?? '#'}",
        '      onClick={e => { if (!target) e.preventDefault(); }}',
        "    >",
        "      {label} →",
        "    </a>",
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
ReactBackend.register("FilterPanel", _generate_filter_panel)
ReactBackend.register("BarChart", _generate_bar_chart)
ReactBackend.register("MetricCard", _generate_metric_card)
ReactBackend.register("Embed", _generate_embed)
ReactBackend.register("SearchBar", _generate_search_bar)
ReactBackend.register("Form", _generate_form)
ReactBackend.register("ExportButton", _generate_export_button)
ReactBackend.register("Drilldown", _generate_drilldown)
