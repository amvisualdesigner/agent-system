"""ComponentNode: structured component tree with ownership boundaries.

ComponentNode replaces the flat AST dict with a tree where each node
owns its props, imports, and composition. No global context mutation.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re

from dataclasses import dataclass, field

from app.renderer.base import FileOp

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = pathlib.Path(__file__).parent / "templates"


@dataclass
class ComponentNode:
    component: str
    file_path: str
    props: dict = field(default_factory=dict)
    imports: list[str] = field(default_factory=list)
    resolved_imports: str | None = None
    children: list[ComponentNode] = field(default_factory=list)
    parent: ComponentNode | None = None
    template: str | None = None
    layout: str | None = None

    def add_child(self, child: ComponentNode) -> None:
        child.parent = self
        self.children.append(child)


def _resolve_template(template_name: str) -> str:
    path = _TEMPLATE_DIR / template_name
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


def _render_template(template: str, context: dict) -> str:
    result = template
    for key, value in context.items():
        placeholder = "__" + key.upper() + "__"
        result = result.replace(placeholder, json.dumps(value))
    return result


def _render_raw_placeholders(template: str, context: dict, keys: set[str]) -> str:
    result = template
    for key in keys:
        value = context.get(key)
        if value is None:
            continue
        placeholder = "__" + key.upper() + "__"
        result = result.replace(placeholder, str(value))
    return result


def _render_for_loop(template: str, context: dict) -> str:
    for_match = re.search(
        r"{% for (\w+) in (\w+) %}(.*?){% endfor %}", template, re.DOTALL
    )
    if for_match:
        var_name = for_match.group(1)
        list_name = for_match.group(2)
        body = for_match.group(3)
        items = context.get(list_name, [])
        expanded = ""
        for item in items:
            item_context = dict(context)
            item_context[var_name] = item
            rendered = _render_template(body, item_context)
            expanded += rendered + "\n"
        template = (
            template[: for_match.start()]
            + expanded.strip()
            + template[for_match.end() :]
        )
    return template


_RAW_PLACEHOLDERS = {
    "TABLE_BODY",
    "COLUMNS_THEAD",
    "EXAMPLE_IMPORTS",
    "LAYOUT_OPEN",
    "LAYOUT_CLOSE",
}

_STRUCTURAL_PLACEHOLDERS = {
    "COMPOSITION",
    "CHILD_IMPORTS",
    "COMPONENT_NAME",
    "RESOLVED_IMPORTS",
}


def _render_composition(children: list[ComponentNode]) -> str:
    """Generate JSX reference tags for child nodes.

    Returns something like:
      <KpiRow metrics={["revenue","growth"]} />
      <Timeseries metric="revenue" />

    NOT the full rendered child file content.
    Pure composition nodes (no template) are skipped.
    """
    parts: list[str] = []
    for child in children:
        if child.template is None:
            continue
        if child.props:
            props_str = " ".join(
                f"{k}={json.dumps(v)}" for k, v in child.props.items()
            )
            parts.append(f"<{child.component} {props_str} />")
        else:
            parts.append(f"<{child.component} />")
    return "\n".join(parts)


def _generate_child_imports(parent: ComponentNode) -> str:
    """Generate import statements for all children of a parent node.

    Imports are derived from the tree structure, not from templates.
    Each child's file path is resolved relative to the parent's directory.
    Pure composition nodes (no template) are skipped.
    """
    parent_dir = os.path.dirname(parent.file_path)
    lines: list[str] = []
    for child in parent.children:
        if child.template is None:
            continue
        rel = os.path.relpath(child.file_path, parent_dir)
        stem = os.path.splitext(rel)[0]
        if not stem.startswith("."):
            stem = "./" + stem
        lines.append(f"import {{ {child.component} }} from '{stem}';")
    return "\n".join(lines)


def _extract_imported_name(line: str) -> str | None:
    """Extract the primary imported name from an import line.

    'import React from ...'     -> 'React'
    'import { Card } from ...'  -> 'Card'
    """
    m = re.search(r"import\s+(?:\{\s*)?(\w+)", line)
    return m.group(1) if m else None


def _resolve_imports(node: ComponentNode) -> list[str]:
    """Resolve and deduplicate all imports for a node.

    Merges example imports (from catalog) with tree-derived
    child imports. Tree-derived imports take priority over
    catalog imports for the same imported name.

    Returns deduplicated, ordered import lines.
    """
    imports: dict[str, str] = {}  # imported_name -> full line (order preserved)

    if node.imports:
        for line in node.imports:
            name = _extract_imported_name(line)
            if name:
                imports[name] = line

    child_imports = _generate_child_imports(node)
    if child_imports:
        for line in child_imports.split("\n"):
            line = line.strip()
            if not line:
                continue
            name = _extract_imported_name(line)
            if name:
                imports[name] = line

    return list(imports.values())


def _build_node_context(node: ComponentNode) -> dict:
    """Build scoped render context from a single ComponentNode.

    Only the node's own props are included — no global merge, no
    child prop propagation. The root node is a pure composition node
    and should NOT receive descendant props.

    Reads node.resolved_imports directly (pre-computed by the
    resolve_imports enrichment pass). No fallback to _resolve_imports
    — the enrichment pass MUST run before emission in the pipeline.
    """
    context: dict = {}

    if node.layout:
        context["LAYOUT_OPEN"] = f"<{node.layout}>"
        context["LAYOUT_CLOSE"] = f"</{node.layout}>"

    context["COMPONENT_NAME"] = node.component

    if node.children:
        context["COMPOSITION"] = _render_composition(node.children)
        context["RESOLVED_IMPORTS"] = node.resolved_imports or ""
    else:
        if node.imports:
            context["EXAMPLE_IMPORTS"] = "\n".join(node.imports)
        else:
            context["EXAMPLE_IMPORTS"] = ""

    for key, value in node.props.items():
        context[key] = value

    if node.component == "AnalyticsTable":
        extra = _build_table_context(node)
        context.update(extra)

    return context


def _build_table_context(node: ComponentNode) -> dict:
    """Pre-compute thead and tbody HTML for AnalyticsTable nodes.

    Per-node equivalent of the old global _build_table_context.
    """
    extra: dict[str, str] = {}
    cols: list[str] = node.props.get("columns", [])
    if not cols:
        return extra

    extra["COLUMNS_THEAD"] = "".join(f"<th>{c}</th>" for c in cols)
    data: list = node.props.get("table_data", [])
    if data:
        rows = []
        for row in data:
            if isinstance(row, dict):
                cells = "".join(
                    f"<td>{json.dumps(row.get(c, ''))}</td>" for c in cols
                )
            elif isinstance(row, (list, tuple)):
                cells = "".join(
                    f"<td>{json.dumps(cell)}</td>" for cell in row
                )
            else:
                cells = ""
            rows.append(f"<tr>{cells}</tr>")
    else:
        rows = [
            "<tr>" + "".join("<td>—</td>" for _ in cols) + "</tr>"
        ] * 3
    extra["TABLE_BODY"] = "<tbody>\n" + "\n".join(rows) + "\n</tbody>"
    return extra


def _deduplicate_imports(template: str, imports_str: str) -> str:
    """Remove import lines already hardcoded in the template."""
    if not imports_str:
        return imports_str
    template_lines = set(template.splitlines())
    extra = [
        line
        for line in imports_str.split("\n")
        if line.strip() and line.strip() not in template_lines
    ]
    return "\n".join(extra) if extra else ""


def _extract_component_name(file_path: str) -> str:
    """Extract component name from a file path.

    'components/KpiRow.tsx' -> 'KpiRow'
    'SalesOverview.tsx' -> 'SalesOverview'
    'src/pages/dashboard/AnalyticsTable.tsx' -> 'AnalyticsTable'
    """
    stem = os.path.splitext(os.path.basename(file_path))[0]
    return stem


def _extract_imports(
    ast: dict, example_context: object | None
) -> list[str]:
    if example_context is not None:
        try:
            if hasattr(example_context, "imports") and example_context.imports:
                return list(example_context.imports)
        except Exception:
            pass
    return ast.get("__example_imports__", [])


def _extract_layout(ast: dict, example_context: object | None) -> str | None:
    layout = ast.get("layout") or ast.get("__layout__")
    if not layout and example_context is not None:
        try:
            if hasattr(example_context, "layouts") and example_context.layouts:
                layout = example_context.layouts[0]
        except Exception:
            pass
    return layout


def build_component_tree(
    ast: dict,
    renderer_config: dict,
    example_context: object | None = None,
) -> ComponentNode:
    """Convert flat AST dict + renderer_config into a ComponentNode tree.

    Rules (Phase 1):
    - renderer_config.files[0] is the root node
    - Remaining files are matched to AST slots by component name
    - If a file's stem matches a slot type, that slot's props are assigned
    - Root that doesn't match any slot is a pure composition node (no props)
    - Leaf nodes that don't match any slot get empty props
    - Single-slot fallback: if only one slot exists and no file matches by
      name, the root receives that slot's props
    - Imports are centralized (all example imports on every node) for Phase 1
    """
    files = renderer_config.get("files", [])
    base_path = renderer_config.get("base_path", "")
    slots = ast.get("nodes", [])

    layout = _extract_layout(ast, example_context)
    example_imports = _extract_imports(ast, example_context)

    if not files:
        raise ValueError("renderer_config has no files")

    # Build slot lookup by type name
    slot_map: dict[str, dict] = {}
    for slot in slots:
        slot_map[slot["type"]] = slot

    unmatched_slots: set[str] = set(slot_map.keys())
    root: ComponentNode | None = None

    for i, file_def in enumerate(files):
        rel_path = file_def["path"]
        full_path = os.path.normpath(os.path.join(base_path, rel_path))
        template_name = file_def.get("template")
        component_name = _extract_component_name(rel_path)

        slot = slot_map.get(component_name)
        if slot:
            props = dict(slot.get("props", {}))
            unmatched_slots.discard(component_name)
        elif i == 0:
            # Single-slot fallback: if exactly one slot exists and no other
            # file has claimed it, assign it to root
            if len(slots) == 1 and component_name not in slot_map:
                slot = slots[0]
                props = dict(slot.get("props", {}))
                unmatched_slots.discard(slot["type"])
            else:
                props = {}
        else:
            props = {}

        node = ComponentNode(
            component=component_name,
            file_path=full_path,
            props=props,
            imports=list(example_imports) if i == 0 else [],
            template=template_name,
            layout=layout if i == 0 else None,
        )

        if i == 0:
            root = node
        else:
            root.add_child(node)

    if root is None:
        raise ValueError("No root node built from renderer_config")

    composition: list[tuple[str, str]] | None = None
    if example_context is not None:
        try:
            comp = getattr(example_context, "composition", None)
            if comp:
                composition = list(comp)
        except Exception:
            pass
    _apply_composition_ordering(root, composition)

    return root


def _collect_all_descendants(root: ComponentNode) -> list[ComponentNode]:
    """BFS collect every node in the tree. Used for enrichment passes."""
    result: list[ComponentNode] = []
    stack = [root]
    while stack:
        node = stack.pop(0)
        result.append(node)
        stack.extend(node.children)
    return result


def resolve_imports(root: ComponentNode) -> None:
    """Pre-compute resolved_imports for every node in the tree.

    Enrichment pass that runs AFTER build_component_tree and BEFORE
    emit_tree. Each node's resolved_imports is derived from its own
    imports + tree-derived child imports, deduplicated against the
    node's template (if any).

    Mutates the tree by setting node.resolved_imports on every node.
    Pure structural pass — no catalog access, no planner state.
    """
    for node in _collect_all_descendants(root):
        resolved = _resolve_imports(node)
        if node.template:
            template_content = _resolve_template(node.template)
            if template_content:
                deduped = _deduplicate_imports(
                    template_content, "\n".join(resolved)
                )
                node.resolved_imports = deduped if deduped else ""
                continue
        node.resolved_imports = "\n".join(resolved) if resolved else ""


def _apply_composition_ordering(
    root: ComponentNode,
    composition: list[tuple[str, str]] | None,
) -> None:
    """Reorder children by composition hints from ExampleContext.

    Composition edges are parent-first: (parent_component, child_component).
    Only applies warnings — never blocks or fails.
    Pure heuristic guidance, not structural enforcement.
    """
    if not composition or not root.children:
        return

    parent_edges = [
        child for parent, child in composition
        if parent == root.component
    ]
    if not parent_edges:
        return

    order = {name: i for i, name in enumerate(parent_edges)}
    root.children.sort(key=lambda c: order.get(c.component, len(order)))

    for child in root.children:
        if child.component not in parent_edges:
            logger.warning(
                "composition_mismatch parent=%s child=%s not in canonical composition",
                root.component, child.component,
            )


def emit_file(node: ComponentNode) -> FileOp | None:
    """Emit a single ComponentNode into a FileOp.

    Core primitive for Phase 4 file-scoped emission.
    Template is a backend serialization layer — NOT the source of truth.
    Returns None for pure composition nodes (no template).

    Raises:
        RuntimeError: if resolve_imports enrichment pass was not run
            before calling emit_file (resolved_imports is None).
    """
    if not node.template:
        return None

    if node.resolved_imports is None:
        raise RuntimeError(
            f"emit_file: resolve_imports enrichment pass was not run. "
            f"node.component={node.component} file_path={node.file_path} "
            f"resolved_imports=None. Call resolve_imports(root) before emit_tree()."
        )

    template = _resolve_template(node.template)
    if not template:
        return None

    context = _build_node_context(node)

    if context.get("RESOLVED_IMPORTS"):
        context["RESOLVED_IMPORTS"] = _deduplicate_imports(
            template, context["RESOLVED_IMPORTS"]
        )
    if context.get("EXAMPLE_IMPORTS"):
        context["EXAMPLE_IMPORTS"] = _deduplicate_imports(
            template, context["EXAMPLE_IMPORTS"]
        )

    rendered = _render_for_loop(template, context)
    rendered = _render_raw_placeholders(
        rendered, context, _RAW_PLACEHOLDERS
    )
    rendered = _render_raw_placeholders(
        rendered, context, _STRUCTURAL_PLACEHOLDERS
    )
    rendered = _render_template(rendered, context)
    rendered = rendered.strip() + "\n"

    return FileOp(action="create", path=node.file_path, content=rendered)


def render_node(node: ComponentNode) -> FileOp | None:
    """Render a single ComponentNode into a FileOp.

    Legacy entry point — delegates to emit_file.
    Kept for backward compatibility with existing tests.
    """
    return emit_file(node)


def emit_tree(node: ComponentNode) -> list[FileOp]:
    """Emit a ComponentNode tree into a list of FileOps.

    Traverses the ownership tree depth-first, emitting each node
    with its own scoped context. Pure composition nodes (no template)
    are skipped — they only exist for structural ownership.

    Uses emit_file as the core primitive (Phase 4 file-scoped emission).
    """
    fileops: list[FileOp] = []

    op = emit_file(node)
    if op is not None:
        fileops.append(op)

    for child in node.children:
        fileops.extend(emit_tree(child))

    return fileops


def render_tree_string(node: ComponentNode, indent: int = 0) -> str:
    """Render a ComponentNode tree as a human-readable string.

    Useful for debugging, observability, and test assertions.
    Shows component name, file path, template, and children count.
    """
    prefix = "  " * indent
    parts: list[str] = []
    comp = node.component
    path = node.file_path
    tmpl = node.template or "(no template)"
    props = node.props
    children_count = len(node.children)
    parts.append(
        f"{prefix}{comp} [{path}] tmpl={tmpl} props={props} children={children_count}"
    )
    for child in node.children:
        parts.append(render_tree_string(child, indent + 1))
    return "\n".join(parts)
