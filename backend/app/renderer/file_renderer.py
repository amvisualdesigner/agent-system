"""File renderer: maps AST + renderer_config → FileOps.

Structural expansion only — no business logic, no branching, no decisions.
Templates use for loops and __VAR__ placeholders with JSON serialization.

Optionally accepts ExampleContext to inject canonical architectural
patterns (imports, layouts) into the template context.
"""

import json
import logging
import os
import re
import pathlib

from app.renderer.base import Renderer, FileOp

logger = logging.getLogger(__name__)


_TEMPLATE_DIR = pathlib.Path(__file__).parent / "templates"


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
    """Replace specific placeholders with raw string (no json.dumps).

    Used for HTML fragments that would be broken by JSON encoding.
    """
    result = template
    for key in keys:
        value = context.get(key)
        if value is None:
            continue
        placeholder = "__" + key.upper() + "__"
        result = result.replace(placeholder, str(value))
    return result


def _render_for_loop(template: str, context: dict) -> str:
    for_match = re.search(r"{% for (\w+) in (\w+) %}(.*?){% endfor %}", template, re.DOTALL)
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
        template = template[:for_match.start()] + expanded.strip() + template[for_match.end():]
    return template


def _build_table_context(ast: dict) -> dict:
    """Pre-compute thead and tbody HTML for AnalyticsTable nodes.

    No decisions — just transforms structured data into HTML fragments
    that the template interpolates deterministically.
    """
    extra: dict[str, str] = {}
    for node in ast.get("nodes", []):
        if node.get("type") != "AnalyticsTable":
            continue
        props = node.get("props", {})
        cols: list[str] = props.get("columns", [])
        if not cols:
            continue
        extra["COLUMNS_THEAD"] = "".join(f"<th>{c}</th>" for c in cols)
        data: list = props.get("table_data", [])
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
            rows = ["<tr>" + "".join("<td>—</td>" for _ in cols) + "</tr>"] * 3
        extra["TABLE_BODY"] = "<tbody>\n" + "\n".join(rows) + "\n</tbody>"
    return extra


class FileRenderer(Renderer):
    def render(
        self,
        ast: dict,
        renderer_config: dict,
        example_context: object | None = None,
    ) -> list[FileOp]:
        # NOTE:
        # Structural hints MUST come from shaped AST (__layout__, etc.)
        # Rendering hints (imports, layout strings) still come from example_context
        fileops = []
        base_path = renderer_config.get("base_path", "")

        # Build example context dict with safe defaults
        ctx_defaults = {
            "EXAMPLE_IMPORTS": "",
            "LAYOUT_OPEN": "",
            "LAYOUT_CLOSE": "",
        }
        if example_context is not None:
            try:
                if hasattr(example_context, "imports") and example_context.imports:
                    ctx_defaults["EXAMPLE_IMPORTS"] = "\n".join(example_context.imports)
                if hasattr(example_context, "layouts") and example_context.layouts:
                    layout = example_context.layouts[0]
                    ctx_defaults["LAYOUT_OPEN"] = f"<{layout}>"
                    ctx_defaults["LAYOUT_CLOSE"] = f"</{layout}>"
            except Exception as e:
                logger.warning("example_context injection failed: %s", e)

        for file_def in renderer_config.get("files", []):
            rel_path = file_def["path"]
            template_name = file_def["template"]
            full_path = os.path.normpath(os.path.join(base_path, rel_path))

            template = _resolve_template(template_name)
            if not template:
                continue
            assert "{% if" not in template, f"conditions forbidden in template {template_name}"

            context = dict(ctx_defaults)
            for node in ast.get("nodes", []):
                for key, value in node.get("props", {}).items():
                    context[key] = value

            # Table pre-computation (deterministic, no branching)
            table_ctx = _build_table_context(ast)
            context.update(table_ctx)

            # Deduplicate EXAMPLE_IMPORTS against template hardcoded imports
            if context.get("EXAMPLE_IMPORTS"):
                template_lines = set(template.splitlines())
                extra = [
                    line for line in context["EXAMPLE_IMPORTS"].split("\n")
                    if line.strip() and line.strip() not in template_lines
                ]
                context["EXAMPLE_IMPORTS"] = "\n".join(extra) if extra else ""

            rendered = _render_for_loop(template, context)
            rendered = _render_raw_placeholders(rendered, context, {"TABLE_BODY", "COLUMNS_THEAD", "EXAMPLE_IMPORTS", "LAYOUT_OPEN", "LAYOUT_CLOSE"})
            rendered = _render_template(rendered, context)
            rendered = rendered.strip() + "\n"

            fileops.append(FileOp(
                action="create",
                path=full_path,
                content=rendered,
            ))

        return fileops
