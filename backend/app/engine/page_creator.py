from __future__ import annotations

import logging
import os

from app.graphir.models import FileOp

logger = logging.getLogger(__name__)


_PAGE_TEMPLATE = '''export default function {name}() {{
  return <div />
}}
'''


_ROUTER_IMPORT_TPL = "import {{ {name} }} from './pages/{context}/{name}';"
_ROUTER_ROUTE_TPL = "  {{ path: '/{context}', element: <{name} /> }},"


def _suggest_page_name(context: str) -> str:
    return f"{context.title()}DashboardPage"


def _find_router_path(workspace: str) -> str | None:
    candidates = [
        "frontend/src/router.tsx",
        "frontend/src/app/router.tsx",
        "src/router.tsx",
        "src/app/router.tsx",
        "router.tsx",
    ]
    for candidate in candidates:
        full = os.path.join(workspace, candidate)
        if os.path.isfile(full):
            return candidate
    return None


def _has_route_for_context(content: str, context: str) -> bool:
    path_pattern = f"/{context.lower()}"
    return path_pattern in content


def _build_router_modification(
    router_path: str,
    context: str,
    page_name: str,
    workspace: str,
) -> tuple[str | None, str | None]:
    full_path = os.path.join(workspace, router_path)
    if not os.path.isfile(full_path):
        return None, None

    with open(full_path) as f:
        content = f.read()

    if _has_route_for_context(content, context):
        logger.info("Route for /%s already exists in %s, skipping", context, router_path)
        return None, None

    import_line = _ROUTER_IMPORT_TPL.format(name=page_name, context=context)
    route_line = _ROUTER_ROUTE_TPL.format(name=page_name, context=context)

    if f"import {{ {page_name} }}" in content:
        return None, None

    new_content = content.rstrip()
    new_content += f"\n\n{import_line}\n"

    routes_tag = "routes: ["
    alt_tag = "createBrowserRouter(["
    inserted = False
    for tag in (routes_tag, alt_tag):
        pos = new_content.find(tag)
        if pos >= 0:
            brace_pos = pos + len(tag)
            indent = ""
            i = brace_pos
            while i > 0 and new_content[i - 1] != '\n':
                i -= 1
                indent += " "
            new_content = new_content[:brace_pos] + f"\n{route_line}\n" + new_content[brace_pos:]
            inserted = True
            break

    if not inserted:
        logger.warning("Could not find route array in %s, route not inserted", router_path)

    return new_content, import_line


def create_page_ops(
    context: str,
    workspace: str,
) -> list[FileOp]:
    ops: list[FileOp] = []
    page_name = _suggest_page_name(context)

    router_path = _find_router_path(workspace)

    pages_dir = "frontend/src/pages"
    if router_path:
        router_dir = os.path.dirname(router_path)
        parent = os.path.commonpath([router_dir, pages_dir]) if os.path.commonprefix([router_dir, pages_dir]) else ""
        if "frontend" in router_path:
            pages_dir = "frontend/src/pages"
        elif router_path.startswith("src/"):
            pages_dir = "src/pages"
        else:
            pages_dir = "pages"

    page_dir = os.path.join(pages_dir, context)
    page_path = os.path.join(page_dir, f"{page_name}.tsx")

    content = _PAGE_TEMPLATE.format(name=page_name)
    ops.append(FileOp(
        action="CREATE",
        path=page_path,
        content=content,
        pipeline_route="page_creator",
        metadata={"page_context": context, "page_name": page_name},
    ))

    if router_path:
        new_content, _ = _build_router_modification(router_path, context, page_name, workspace)
        if new_content is not None:
            ops.append(FileOp(
                action="MODIFY",
                path=router_path,
                content=new_content,
                pipeline_route="page_creator",
                metadata={"page_context": context, "page_name": page_name, "change": "add_route"},
            ))

    return ops


def create_page_ops_dry(context: str, workspace: str) -> list[dict]:
    page_name = _suggest_page_name(context)
    router_path = _find_router_path(workspace)

    pages_dir = "frontend/src/pages"
    page_dir = os.path.join(pages_dir, context)
    page_path = os.path.join(page_dir, f"{page_name}.tsx")

    ops = [
        FileOp(
            action="CREATE",
            path=page_path,
            content=_PAGE_TEMPLATE.format(name=page_name),
            pipeline_route="page_creator",
        ).to_dict(),
    ]

    if router_path:
        ops.append(FileOp(
            action="MODIFY",
            path=router_path,
            content="",
            pipeline_route="page_creator",
            metadata={"change": "add_route"},
        ).to_dict())

    return ops
