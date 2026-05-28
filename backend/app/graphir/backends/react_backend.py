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
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.graphir.models import GraphIR, GraphIRLayout, GraphIRNode, LayoutConstraint
from app.graphir.backends.base import BackendRenderer, BackendConfig
from app.graphir.models import FileOp
from app.graphir.compiler import UIIRCompiler
from app.graphir.path_resolver import FilePathResolver
from app.graphir.ui_ir import UIComponentTree, UIComponentNode, UIGeneratorContext

ComponentGenerator = Callable[
    [GraphIRNode, list[LayoutConstraint], "ReactBackend", BackendConfig],
    str,
]


class RenderTraceViolation(Exception):
    """Raised when RenderTrace lifecycle invariant is violated.

    This is a HARD fail — not an audit flag. RenderTrace must be a
    deterministic state machine. Any out-of-order or invalid transition
    means the pipeline state is unrecoverable.
    """


@dataclass
class RenderTrace:
    """Single observation in the render lifecycle.

    phases:
      "entered"  → node entered the render loop
      "emitted"  → props sent to runtime (FileOp created)

    Every node ends as either "entered" (not rendered) or "emitted" (success).
    """
    node_id: str
    phase: str
    component: str | None = None
    props: dict | None = None
    timestamp: float = 0.0


def _flatten_tree(root: UIComponentNode) -> list[UIComponentNode]:
    """BFS flatten: root first, then children depth-first."""
    result: list[UIComponentNode] = []
    def walk(n: UIComponentNode) -> None:
        result.append(n)
        for c in n.children:
            walk(c)
    walk(root)
    return result


class ReactBackend(BackendRenderer):
    """React/TSX backend — generates .tsx files from GraphIR.

    Uses registered ComponentGenerator functions for each node type.
    Register new generators with ReactBackend.register(type_name, generator).

    Built-in generators cover: Page, KpiRow, Timeseries, AnalyticsTable.
    """

    _generators: dict[str, ComponentGenerator] = {}
    _render_traces: list[RenderTrace] = []
    _render_traces_by_run: dict[str, list[RenderTrace]] = {}
    _emit_log: dict[str, list[dict]] = {}  # debug dump only — NOT used in metrics
    _current_run: str = ""
    _entered_nodes: set[str] = set()  # lifecycle enforcement set

    @classmethod
    def reset_emit_log(cls, run_id: str = "") -> None:
        cls._current_run = run_id if run_id else "unknown"
        cls._emit_log[cls._current_run] = []

    @classmethod
    def reset_traces(cls, run_id: str = "") -> None:
        cls._render_traces = []
        cls._entered_nodes.clear()
        if run_id:
            cls._render_traces_by_run[run_id] = []

    @classmethod
    def add_trace(cls, node_id: str, phase: str, component: str | None = None, props: dict | None = None) -> None:
        if phase == "emitted" and node_id not in cls._entered_nodes:
            raise RenderTraceViolation(
                f"emit({node_id}) without prior enter({node_id}) — "
                f"RenderTrace lifecycle violated. "
                f"entered_nodes={sorted(cls._entered_nodes)}"
            )
        if phase == "entered":
            cls._entered_nodes.add(node_id)
        cls._render_traces.append(RenderTrace(
            node_id=node_id, phase=phase, component=component,
            props=props, timestamp=time.time(),
        ))

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
        """GraphIR → FileOps via UIIRCompiler + render_tree.

        This is the entry point. Delegates compilation to UIIRCompiler
        and rendering to render_tree. No direct access to graph.nodes.
        """
        tree = UIIRCompiler.compile(graph, layout)
        return self.render_tree(tree, config)

    def render_tree(
        self,
        tree: UIComponentTree,
        config: BackendConfig,
    ) -> list[FileOp]:
        """UIComponentTree → FileOps. NO access to GraphIR.

        Consumes only the compiled UI tree. Each node is wrapped in
        UIGeneratorContext to interface with existing generators.
        """
        fileops: list[FileOp] = []
        for uinode in _flatten_tree(tree.root):
            ReactBackend.add_trace(uinode.id, "entered", component=uinode.component)
            generator = self._generators.get(uinode.component)
            if generator is None:
                continue

            ctx = UIGeneratorContext(
                id=uinode.id,
                type=uinode.component,
                data=dict(uinode.props),
            )
            content = generator(ctx, uinode.layout_hints, self, config)

            if uinode.children:
                composition = ReactBackend._render_children(uinode.children)
                content = self._inject_composition(content, composition)

            file_path = FilePathResolver.resolve(ctx, config)
            fileops.append(FileOp(action="create", path=file_path, content=content))
            ReactBackend.add_trace(uinode.id, "emitted", component=uinode.component, props=dict(uinode.props))

        return fileops

    def _resolve_file_path(self, node, config: BackendConfig) -> str:
        return FilePathResolver.resolve(node, config)

    @staticmethod
    def _render_children(children: list[UIComponentNode]) -> str:
        """UIComponentNode list → mounted JSX string.

        Uses _emit() for props. Applies layout wrappers from layout_hints.
        """
        parts: list[str] = []
        for child in children:
            ReactBackend._emit_log.setdefault(ReactBackend._current_run, []).append(
                {"component": child.component, "props": dict(child.props), "node_id": child.id}
            )
            props_str = ReactBackend._emit(child.props)
            child_tag = (
                f"<{child.component} {props_str} />" if props_str
                else f"<{child.component} />"
            )
            if child.layout_hints:
                open_tag, close_tag = ReactBackend._layout_to_wrapper(child.layout_hints)
                parts.append(f"{open_tag}\n        {child_tag}\n      {close_tag}")
            else:
                parts.append(child_tag)
        return "\n".join(parts)

    @staticmethod
    def _emit(props: dict[str, Any]) -> str:
        """Framework-specific: dict → JSX attribute string.

        TOTAL emission: every key in props maps to exactly one JSX attribute.
        None emits as {null} via json.dumps. No silent skips. No filtering.

        This is the LAST transformation in the pipeline.
        The contract is UIComponentTree; _emit is just the last mile.

        Instrumented: logs emitted props to _emit_log for audit.
        """
        ReactBackend._emit_log.setdefault(ReactBackend._current_run, []).append(dict(props))
        if not props:
            return ""
        parts = []
        for k, v in props.items():
            if isinstance(v, str):
                parts.append(f'{k}="{v}"')
            elif isinstance(v, bool):
                parts.append(f"{k}={str(v).lower()}")
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
