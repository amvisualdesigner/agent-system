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
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar

logger = logging.getLogger(__name__)

from app.binding.models import ResolvedBindings
from app.graphir.compiler import UIIRCompiler
from app.graphir.models import FileOp, GraphIR, GraphIRNode, LayoutConstraint
from app.graphir.layout import GraphIRLayout
from app.graphir.backends.base import BackendRenderer, BackendConfig
from app.graphir.path_resolver import FilePathResolver
from app.graphir.ui_ir import (
    UIComponentNode,
    UIComponentTree,
    UIGeneratorContext,
)
from app.signature.prop_mapper import (
    MISSING_REQUIRED_PROPS,
    DataSourceIR,
    JSExpression,
    _HOOK_IMPORT_MAP,
    _REACT_HOOK_MAP,
    load_page_data_source,
)

ComponentGenerator = Callable[
    [GraphIRNode, list[LayoutConstraint], "ReactBackend", BackendConfig],
    str,
]


@dataclass(frozen=True)
class JSVariable:
    """A JavaScript variable reference — emitted as {name} not "string".

    Used during hook hoisting to replace HookBinding instances in child props.
    The renderer emits this as data={_KpiRow_data} (braces, JS expression).
    """
    name: str


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
    last_ui_tree: UIComponentTree | None = None  # last compiled tree (for audit)

    @classmethod
    def reset_emit_log(cls, run_id: str = "") -> None:
        cls._current_run = run_id if run_id else "unknown"
        cls._emit_log[cls._current_run] = []

    @classmethod
    def reset_traces(cls, run_id: str = "") -> None:
        cls._render_traces = []
        cls._entered_nodes.clear()
        cls.last_ui_tree = None
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
        resolved_bindings: ResolvedBindings | None = None,
    ) -> list[FileOp]:
        """GraphIR → FileOps via UIIRCompiler + render_tree.

        This is the entry point. Delegates compilation to UIIRCompiler
        and rendering to render_tree. No direct access to graph.nodes.

        Props are pre-resolved by BindingResolver — contract_params never
        reach the compiler or renderer (PR1 Binding Resolution Architecture).
        """
        tree = UIIRCompiler.compile(
            graph, layout,
            resolved_bindings=resolved_bindings,
            component_signatures=config.component_signatures,
        )
        ReactBackend.last_ui_tree = tree
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

            # instance_only: capability exists in repo, requested as CREATE.
            # Skip file generation — the implementation file stays untouched.
            # The node still participates in parent composition (via _render_children).
            if uinode.instance_only:
                ReactBackend.add_trace(uinode.id, "skipped_instance_only",
                    component=uinode.component)
                continue

            # BINDING_MISSING: prop has no binding at render time.
            # If the missing prop is REQUIRED → HARD error (MISSING_REQUIRED_PROPS).
            # If the missing prop is optional → skip node with warning.
            if uinode.binding_missing_props:
                required = ReactBackend._known_required_props(uinode.component, config)
                missing_required = [p for p in uinode.binding_missing_props
                                    if required and p in required]
                if missing_required:
                    raise RuntimeError(
                        f"MISSING_REQUIRED_PROPS:{uinode.component}"
                        f":{','.join(missing_required)}"
                        f":binding_missing at render time — no fallback available"
                    )
                ReactBackend.add_trace(uinode.id, "binding_missing",
                    component=uinode.component,
                    props={"missing_props": list(uinode.binding_missing_props)})
                logger.warning(
                    "OPTIONAL_BINDING_MISSING: %s missing optional props: %s — skipping render.",
                    uinode.component, uinode.binding_missing_props,
                )
                continue

            generator = self._generators.get(uinode.component)
            if generator is None:
                continue

            ctx = UIGeneratorContext(
                id=uinode.id,
                type=uinode.component,
                data=dict(uinode.props),
            )
            content = generator(ctx, uinode.layout_hints, self, config)

            # PR3: props come from ResolvedBindings.component_props (set in compiler).
            # _distribute_page_slices deleted — all resolution is pre-compilation.
            page_hook_decl: str | None = None
            if uinode.component == "Page" and tree.page_data_source:
                page_hook_decl = ReactBackend._page_hook_declaration(tree.page_data_source)
                hook_import = ReactBackend._page_hook_import(tree.page_data_source)
                if hook_import and hook_import not in uinode.data_imports:
                    uinode.data_imports = tuple(list(uinode.data_imports) + [hook_import])

            # Phase 5: inject data_access.json imports (Page only in Phase 6)
            if uinode.data_imports:
                content = ReactBackend._inject_data_imports(content, uinode.data_imports)

            composition = ReactBackend._render_children(uinode.children, config) if uinode.children else ""
            content = self._inject_composition(content, composition)

            # Phase 6: inject Page hook declaration after composition injected
            if page_hook_decl:
                content = ReactBackend._inject_hook_declarations(content, [page_hook_decl])

            file_path = FilePathResolver.resolve(ctx, config)

            # Fix 1 — Export normalization: when the target filename differs from
            # the component type (e.g. Page type written to SalesOverviewPage.tsx),
            # rename the export and interface to match the file basename.
            file_basename = os.path.splitext(os.path.basename(file_path))[0]
            if uinode.component != file_basename and file_basename:
                content = self._normalize_export_name(content, uinode.component, file_basename)

            fileops.append(FileOp(action="create", path=file_path, content=content, pipeline_route="renderer"))
            ReactBackend.add_trace(uinode.id, "emitted", component=uinode.component, props=dict(uinode.props))

        return fileops

    def _resolve_file_path(self, node, config: BackendConfig) -> str:
        return FilePathResolver.resolve(node, config)

    @staticmethod
    def _known_prop_names(component_type: str, config: BackendConfig) -> set[str] | None:
        """Return set of known prop names from a component's signature, or None if unknown."""
        sig = (config.component_signatures or {}).get(component_type, {})
        pn = sig.get("prop_names")
        return set(pn) if pn else None

    @staticmethod
    def _known_required_props(component_type: str, config: BackendConfig) -> list[str] | None:
        """Return list of required prop names from a component's signature, or None if unknown."""
        sig = (config.component_signatures or {}).get(component_type, {})
        rp = sig.get("required_props")
        return rp if rp else None

    @staticmethod
    def _inject_data_imports(content: str, data_imports: tuple[str, ...]) -> str:
        """Insert data_access.json import lines after the last existing import.
        
        Deduplicates against existing imports. No-op if data_imports is empty.
        """
        if not data_imports:
            return content
        existing_imports: set[str] = set()
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("import "):
                existing_imports.add(stripped)
        new_imports = [imp for imp in data_imports if imp.strip() not in existing_imports]
        if not new_imports:
            return content
        lines = content.split("\n")
        last_import = -1
        for i, line in enumerate(lines):
            if line.strip().startswith("import "):
                last_import = i
        if last_import >= 0:
            insert_pos = last_import + 1
            while insert_pos < len(lines) and lines[insert_pos].strip() == "":
                insert_pos += 1
            result = lines[:insert_pos] + new_imports + [""] + lines[insert_pos:]
            return "\n".join(result)
        return "\n".join(new_imports + [""] + lines)

    @staticmethod
    def _page_hook_declaration(ds: DataSourceIR) -> str | None:
        """Generate the hook declaration statement for the Page data source.

        Returns e.g. "const _pageData = useDashboardData();"
        or None if the data source type has no hook mapping.
        """
        hook_name = _REACT_HOOK_MAP.get(ds.type)
        if not hook_name:
            return None
        return f"const _pageData = {hook_name}();"

    @staticmethod
    def _page_hook_import(ds: DataSourceIR) -> str | None:
        """Generate the import statement for the Page data source hook.

        Returns e.g. "import { useDashboardData } from '@/hooks/useDashboardData'"
        or None if no import mapping exists.
        """
        hook_name = _REACT_HOOK_MAP.get(ds.type)
        if not hook_name:
            return None
        return _HOOK_IMPORT_MAP.get(hook_name)

    @staticmethod
    def _inject_hook_declarations(content: str, decls: list[str]) -> str:
        """Insert hook variable declarations into the function body.

        Finds the first return statement and inserts declarations before it,
        after the opening brace of the arrow function body.
        """
        if not decls:
            return content
        lines = content.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("return"):
                indent = line[:len(line) - len(line.lstrip())]
                decl_lines = [f"{indent}{d}" for d in decls]
                result = lines[:i] + decl_lines + lines[i:]
                return "\n".join(result)
        return content

    @staticmethod
    def _render_children(children: list[UIComponentNode], config: BackendConfig) -> str:
        """UIComponentNode list → mounted JSX string.

        Uses _emit() for props. Filters unknown props when child has a known signature.
        Applies layout wrappers from layout_hints.

        RENDER IS DUMB: no prop resolution, no fallback inference.
        required_props MUST be present — if missing, HARD error.
        optional_props MAY use fallback_props ONLY when contract defaults +
        bindings are absent (strict boundary).
        """
        parts: list[str] = []
        for child in children:
            emit_props = child.props or {}

            # Required props guard: if compilation gate somehow missed a
            # required prop, we catch it here as a hard error.
            required = ReactBackend._known_required_props(child.component, config)
            if required is not None:
                missing_required = [r for r in required if r not in emit_props]
                if missing_required:
                    raise RuntimeError(
                        f"MISSING_REQUIRED_PROPS_AT_RENDER:{child.component}"
                        f":{','.join(missing_required)}"
                        f":available={list(emit_props.keys())}"
                    )

            # fallback_props for OPTIONAL props only — when contract defaults
            # absent AND no binding exists AND prop is optional.
            if required is not None and child.fallback_props:
                optional_props = child.fallback_props  # for optional-only resolution
            else:
                optional_props = {}

            ReactBackend._emit_log.setdefault(ReactBackend._current_run, []).append(
                {"component": child.component, "props": {
                    k: v.code if isinstance(v, JSExpression)
                    else v.name if isinstance(v, JSVariable)
                    else v
                    for k, v in emit_props.items()
                }, "node_id": child.id}
            )
            # Filter props against known signature to avoid type mismatches
            known = ReactBackend._known_prop_names(child.component, config)
            filtered_props = {k: v for k, v in emit_props.items() if known is None or k in known} if emit_props else emit_props
            props_str = ReactBackend._emit(filtered_props)
            child_tag = (
                f"<{child.component} {props_str} />" if props_str
                else f"<{child.component} />"
            )
            if not props_str and known and known is not None:
                logger.warning(
                    "EMIT-EMPTY: %s (id=%s) emitted bare tag (no props). "
                    "Known prop_names=%s but props=%s. Check data_access.json slices.",
                    child.component, child.id, known, emit_props,
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
        No silent skips. No filtering.

        Type rules (sole quoter — no pre-quoting from upstream):
          - JSExpression → {code} (raw JS, from data_access.json)
          - str         → "value"
          - bool        → {true|false}
          - None        → {null}
          - other       → {json.dumps(v)}  (numbers, arrays, objects)

        This is the LAST transformation in the pipeline.
        The contract is UIComponentTree; _emit is just the last mile.

        Instrumented: logs emitted props to _emit_log for audit.
        """
        ReactBackend._emit_log.setdefault(ReactBackend._current_run, []).append(
            {
                k: v.code if isinstance(v, JSExpression)
                else v.name if isinstance(v, JSVariable)
                else v
                for k, v in props.items()
            }
        )
        if not props:
            return ""
        parts = []
        for k, v in props.items():
            if isinstance(v, JSExpression):
                parts.append(f"{k}={{{v.code}}}")
            elif isinstance(v, JSVariable):
                parts.append(f"{k}={{{v.name}}}")
            elif isinstance(v, str):
                parts.append(f'{k}="{v}"')
            elif isinstance(v, bool):
                parts.append(f"{k}={str(v).lower()}")
            elif v is None:
                parts.append(f"{k}={{null}}")
            else:
                parts.append(f"{k}={{{json.dumps(v)}}}")
        return " ".join(parts)

    @staticmethod
    def _inject_composition(content: str, composition: str) -> str:
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

    @staticmethod
    def _normalize_export_name(content: str, source_type: str, target_name: str) -> str:
        """Rename exports and interfaces from source_type to target_name.

        When a Page component (type 'Page') is written to SalesOverviewPage.tsx,
        this rewrites:
          export const Page: React.FC<PageProps> → export const SalesOverviewPage: React.FC<SalesOverviewPageProps>
          interface PageProps → interface SalesOverviewPageProps
          export function Page → export function SalesOverviewPage
        """
        if not source_type or not target_name or source_type == target_name:
            return content
        # Fix: export const X: React.FC<XProps>
        content = re.sub(
            rf'(\bexport const ){re.escape(source_type)}'
            rf'(: React\.FC<){re.escape(source_type)}(Props>)',
            rf'\g<1>{target_name}\g<2>{target_name}\g<3>',
            content,
        )
        # Fix: export const X: React.FC (no Props interface)
        content = re.sub(
            rf'(\bexport const ){re.escape(source_type)}(: React\.FC)',
            rf'\g<1>{target_name}\g<2>',
            content,
        )
        # Fix: export function X
        content = re.sub(
            rf'(\bexport function ){re.escape(source_type)}(?=\s*\()',
            rf'\g<1>{target_name}',
            content,
        )
        # Fix: interface XProps
        content = re.sub(
            rf'(\binterface ){re.escape(source_type)}(Props\b)',
            rf'\g<1>{target_name}\g<2>',
            content,
        )
        return content


# ── Built-in Component Generators ──────────────────────────────────────


def _sig_iface_name(sig: dict) -> str | None:
    """Extract interface/type name from a signature props block."""
    if not sig or not sig.get("props"):
        return None
    m = re.search(r'\b(?:interface|type)\s+(\w+)', sig["props"])
    return m.group(1) if m else None


def _build_signature_prefix(node_type: str, config: BackendConfig) -> tuple[str | None, str | None, list[str] | None]:
    """Return (props_block, iface_name, extra_types_lines) from config signatures, or (None, None, None)."""
    sig = (config.component_signatures or {}).get(node_type, {})
    if sig and sig.get("props"):
        iface = _sig_iface_name(sig)
        extra: list[str] = []
        for t in sig.get("extra_types", []):
            extra.append(t)
            extra.append("")
        return sig["props"], iface, extra
    return None, None, None


def _render_signature(
    node_type: str,
    config: BackendConfig,
    body_lines: list[str],
    destructure: str = "_props",
) -> str | None:
    """Build full file content from signature override, or None if not available.

    Produces: imports + blank + extra_types + props_block + export_with_body
    ``destructure`` controls the parameter pattern, e.g. ``"{ metrics }"`` or ``""``.
    """
    sig = (config.component_signatures or {}).get(node_type, {})
    if not sig or not sig.get("props"):
        return None
    iface = _sig_iface_name(sig)
    if not iface:
        return None

    lines: list[str] = []
    imps = sig.get("imports", [])
    kept_imports: list[str] = []
    for imp in imps:
        if "from 'react'" in imp or 'from "react"' in imp:
            kept_imports.append(imp)
        elif imp.startswith("import type"):
            kept_imports.append(imp)
    if not kept_imports:
        kept_imports.append("import React from 'react';")
    if kept_imports:
        lines.extend(kept_imports)
        lines.append("")
    for t in sig.get("extra_types", []):
        lines.append(t)
        lines.append("")
    lines.append(sig["props"])
    lines.append("")
    params = destructure if destructure else ""
    lines.append(f"export const {node_type}: React.FC<{iface}> = ({params}) => {{")
    lines.extend(f"  {l}" if l else "" for l in body_lines)
    lines.append("};")
    lines.append("")
    return "\n".join(lines)


def _generate_page(
    node: GraphIRNode,
    constraints: list[LayoutConstraint],
    backend: ReactBackend,
    config: BackendConfig,
) -> str:
    open_tag, close_tag = ReactBackend._layout_to_wrapper(constraints)
    body = [
        "  return (",
        f"    {open_tag}",
        "      __COMPOSITION__",
        f"    {close_tag}",
        "  );",
    ]
    sig = _render_signature(node.type, config, body, destructure="")
    if sig:
        return sig

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
    body = [
        "  return (",
        '    <div className="kpi-row">',
        map_block,
        "    </div>",
        "  );",
    ]
    sig = _render_signature(node.type, config, body, destructure="{ metrics }")
    if sig:
        return sig

    lines = [
        "import React from 'react';",
        "import { Card } from '@/components/ui/Card';",
        "",
        f"interface {node.type}Props {{",
        "  metrics: string[];",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ metrics }}) => {{",
        *body,
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
    body = [
        "  return (",
        "    <Card>",
        '      <div className="timeseries-chart">',
        '        <h3>{metric} over time</h3>',
        "      </div>",
        "    </Card>",
        "  );",
    ]
    sig = _render_signature(node.type, config, body, destructure="{ metric }")
    if sig:
        return sig

    lines = [
        "import React from 'react';",
        "import { Card } from '@/components/ui/Card';",
        "",
        f"interface {node.type}Props {{",
        "  metric: string;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ metric }}) => {{",
        *body,
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
    body = [
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
        "      </table>",
        "    </Card>",
        "  );",
    ]
    sig = _render_signature(node.type, config, body, destructure="{ columns, rows = [] }")
    if sig:
        return sig

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
        *body,
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
    body = [
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
    ]
    sig = _render_signature(node.type, config, body, destructure="{ filters = [] }")
    if sig:
        return sig

    lines = [
        "import React, { useState } from 'react';",
        "import { Card } from '@/components/ui/Card';",
        "",
        f"interface {node.type}Props {{",
        "  filters?: string[];",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ filters = [] }}) => {{",
        *body,
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
    body = [
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
    ]
    sig = _render_signature(node.type, config, body, destructure="{ categories = [], values = [] }")
    if sig:
        return sig

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
        *body,
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
    body = [
        "  return (",
        "    <Card>",
        '      <div className="metric-card">',
        '        {value !== undefined && <span className="metric-value">{value}</span>}',
        '        {label && <span className="metric-label">{label}</span>}',
        "      </div>",
        "    </Card>",
        "  );",
    ]
    sig = _render_signature(node.type, config, body, destructure="{ value, label }")
    if sig:
        return sig

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
        *body,
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
    body = [
        "  return (",
        "    <div className=\"embed-container\">",
        '      {src ? <iframe src={src} title={title ?? "embedded content"} /> : null}',
        "    </div>",
        "  );",
    ]
    sig = _render_signature(node.type, config, body, destructure="{ src, title }")
    if sig:
        return sig

    lines = [
        "import React from 'react';",
        "",
        f"interface {node.type}Props {{",
        "  src?: string;",
        "  title?: string;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ src, title }}) => {{",
        *body,
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
    body = [
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
    ]
    sig = _render_signature(node.type, config, body, destructure="{ placeholder = 'Search...', onSearch }")
    if sig:
        return sig

    lines = [
        "import React, { useState } from 'react';",
        "",
        f"interface {node.type}Props {{",
        "  placeholder?: string;",
        "  onSearch?: (query: string) => void;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ placeholder = 'Search...', onSearch }}) => {{",
        *body,
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
    body = [
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
    ]
    sig = _render_signature(node.type, config, body, destructure="{ fields = [], onSubmit }")
    if sig:
        return sig

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
        *body,
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
    body = [
        "  return (",
        '    <div className="export-button">',
        "      <button onClick={onExport}>Export as {format.toUpperCase()}</button>",
        "    </div>",
        "  );",
    ]
    sig = _render_signature(node.type, config, body, destructure="{ format = 'csv', onExport }")
    if sig:
        return sig

    lines = [
        "import React from 'react';",
        "",
        f"interface {node.type}Props {{",
        "  format?: string;",
        "  onExport?: () => void;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ format = 'csv', onExport }}) => {{",
        *body,
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
    body = [
        "  return (",
        "    <a",
        '      className="drilldown-link"',
        "      href={target ?? '#'}",
        '      onClick={e => { if (!target) e.preventDefault(); }}',
        "    >",
        "      {label} →",
        "    </a>",
        "  );",
    ]
    sig = _render_signature(node.type, config, body, destructure="{ label = 'View details', target }")
    if sig:
        return sig

    lines = [
        "import React from 'react';",
        "",
        f"interface {node.type}Props {{",
        "  label?: string;",
        "  target?: string;",
        "}",
        "",
        f"export const {node.type}: React.FC<{node.type}Props> = ({{ label = 'View details', target }}) => {{",
        *body,
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


# ── Datasource infrastructure generation (bootstrap, not per-apply) ────────


def generate_datasource_artifacts(
    contract: "DatasourceContract",
    workspace_root: str,
) -> list[FileOp]:
    """Generate datasource infrastructure files from contract.

    Called once per workspace (bootstrap), not per plan execution.
    Generates TypeScript types + hook implementation.

    Returns FileOps with path relative to workspace_root.
    """
    from app.datasource.contract import DatasourceContract

    fileops: list[FileOp] = []

    # ── types/dashboard.ts ──
    type_lines: list[str] = [
        "// Auto-generated from datasource contract. Do not edit manually.",
        "// Change backend/data/datasource.json to modify.",
        "",
    ]
    for t in contract.types:
        type_lines.append(t.to_typescript())
        type_lines.append("")

    # Build DashboardData interface from fields
    dashboard_lines = ["export interface DashboardData {"]
    nested_groups: dict[str, list[tuple[str, str]]] = {}
    top_level: list[tuple[str, str]] = []
    for f in contract.fields:
        parts = f.path_parts
        if len(parts) == 1:
            top_level.append((parts[0], f.type_ref))
        else:
            nested_groups.setdefault(parts[0], []).append((".".join(parts[1:]), f.type_ref))

    for name, type_ref in sorted(top_level):
        dashboard_lines.append(f"  {name}: {type_ref};")
    for group_name, sub_fields in sorted(nested_groups.items()):
        dashboard_lines.append(f"  {group_name}: {{")
        for sub_name, sub_type in sorted(sub_fields):
            dashboard_lines.append(f"    {sub_name}: {sub_type};")
        dashboard_lines.append("  };")
    dashboard_lines.append("}")
    type_lines.extend(dashboard_lines)
    type_lines.append("")

    type_content = "\n".join(type_lines)
    type_path = os.path.join("frontend", "src", "types", "dashboard.ts")
    fileops.append(FileOp(
        action="create",
        path=type_path,
        content=type_content,
        pipeline_route="infrastructure",
    ))

    # ── hooks/useDashboardData.ts ──
    hook_lines: list[str] = [
        "// Auto-generated from datasource contract. Do not edit manually.",
        "// Change backend/data/datasource.json to modify.",
        "",
        "import type { DashboardData } from '@/types/dashboard';",
        "",
        f"export function {contract.hook_name}(): DashboardData {{",
    ]

    if contract.mock_data:
        raw = json.dumps(contract.mock_data, indent=2)
        mock_lines = raw.split("\n")
        # first line is opening brace -> "  return {"
        hook_lines.append("  return {")
        for line in mock_lines[1:-1]:
            hook_lines.append(f"    {line}")
        # last line is closing brace -> "  };"
        hook_lines.append("  };")
    else:
        hook_lines.append("  return {};")

    hook_lines.append("}")
    hook_lines.append("")

    hook_content = "\n".join(hook_lines)
    hook_path = os.path.join("frontend", "src", "hooks", "useDashboardData.ts")
    fileops.append(FileOp(
        action="create",
        path=hook_path,
        content=hook_content,
        pipeline_route="infrastructure",
    ))

    logger.info(
        "DATASOURCE_BOOTSTRAP: generated %d files (types=%s, hook=%s)",
        len(fileops), type_path, hook_path,
    )
    return fileops
