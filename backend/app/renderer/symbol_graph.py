"""SymbolGraph: structural integrity checker over (tree + config).

Post-render validation pass. Pure validation — no mutation, no resolution.
Part of the deterministic semantic rendering pipeline.

Contracts:
- ComponentNode Tree = SOURCE OF TRUTH
- RESOLVED_IMPORTS = deterministic function(tree, catalog)
- emit_tree() consumes ONLY resolved state
- SymbolGraph = post-render validation (NOT construction, NOT mutation, NOT resolution)
"""

import re

from app.renderer.component_node import (
    ComponentNode,
    _extract_imported_name,
    _resolve_imports,
)


def _is_project_import(line: str) -> bool:
    m = re.search(r"from\s+['\"]([^'\"]+)['\"]", line)
    if not m:
        return False
    source = m.group(1)
    return source.startswith(("./", "../"))


def _collect_nodes(root: ComponentNode) -> dict[str, ComponentNode]:
    """DFS collect: component_name -> node. Strictly O(n). No validation logic."""
    nodes: dict[str, ComponentNode] = {}
    stack = [root]
    while stack:
        node = stack.pop()
        nodes[node.component] = node
        stack.extend(node.children)
    return nodes


def validate_symbol_graph(
    root: ComponentNode,
    renderer_config: dict,
    composition: list[tuple[str, str]] | None = None,
    known_layouts: set[str] | None = None,
) -> tuple[bool, str]:
    """Structural consistency verifier over (tree + config).

    Tree-aware. Requires root — config-only validation misses graph-level issues.

    Checks:
    1. Phantom imports — project imports referencing a component
       not present in the node set
    2. Self-imports — node importing its own component name
    3. Orphan composition — composition references a component not in
       the tree (excluding known layouts)

    Returns (True, "ok") or (False, "reason: detail").
    """
    nodes = _collect_nodes(root)
    known_components = set(nodes.keys())
    layouts = known_layouts or set()

    issues: list[str] = []

    for node in nodes.values():
        resolved = _resolve_imports(node)
        for line in resolved:
            if not _is_project_import(line):
                continue
            name = _extract_imported_name(line)
            if not name:
                continue
            if name == node.component:
                issues.append(
                    f"self_import: {node.file_path} imports itself ('{line}')"
                )
            elif name not in known_components:
                issues.append(
                    f"phantom_import: {node.file_path} imports '{name}' "
                    f"but no component in tree produces it ('{line}')"
                )

    if composition:
        all_targets = {target for _, target in composition}
        for target in sorted(all_targets):
            if target not in known_components and target not in layouts:
                issues.append(
                    f"orphan_composition: '{target}' referenced in composition "
                    f"but no component in tree produces it"
                )

    if issues:
        return False, "; ".join(issues)
    return True, "ok"
