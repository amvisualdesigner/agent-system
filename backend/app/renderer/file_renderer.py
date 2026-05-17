"""File renderer: maps AST + renderer_config → FileOps.

Structural expansion only — no business logic, no branching, no decisions.
Templates use for loops and __VAR__ placeholders with JSON serialization.
"""

import json
import os
import re
import pathlib

from app.renderer.base import Renderer, FileOp


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


class FileRenderer(Renderer):
    def render(self, ast: dict, renderer_config: dict) -> list[FileOp]:
        fileops = []
        base_path = renderer_config.get("base_path", "")

        for file_def in renderer_config.get("files", []):
            rel_path = file_def["path"]
            template_name = file_def["template"]
            full_path = os.path.normpath(os.path.join(base_path, rel_path))

            template = _resolve_template(template_name)
            if not template:
                continue
            assert "{% if" not in template, f"conditions forbidden in template {template_name}"

            context = {}
            for node in ast.get("nodes", []):
                for key, value in node.get("props", {}).items():
                    context[key] = value

            rendered = _render_for_loop(template, context)
            rendered = _render_template(rendered, context)
            rendered = rendered.strip() + "\n"

            fileops.append(FileOp(
                action="create",
                path=full_path,
                content=rendered,
            ))

        return fileops
