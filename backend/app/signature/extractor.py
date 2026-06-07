from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_IMPORT_PATTERN = re.compile(
    r'^import\s+.*?from\s+[\'\"].+?[\'\"]\s*;?\s*$',
    re.MULTILINE,
)
_ALL_TYPE_PATTERN = re.compile(
    r'\b(interface|type)\s+(\w+)',
)
_COMPONENT_EXPORT_PATTERN = re.compile(
    r'export\s+(?:function|const)\s+(\w+)',
)
_PROP_DETAIL_PATTERN = re.compile(
    r'^\s+(\w+)(\??)\s*:\s*(.+?);?\s*$',
    re.MULTILINE,
)


def extract_signatures(workspace_path: str) -> dict[str, dict[str, Any]]:
    signatures: dict[str, dict[str, Any]] = {}

    tsx_files = _find_tsx_files(workspace_path)
    if not tsx_files:
        logger.info("No .tsx files found in %s", workspace_path)
        return signatures

    for filepath in tsx_files:
        sig = _extract_single_file(filepath)
        if sig:
            signatures[sig["component_name"]] = sig

    logger.info("Extracted %d component signatures from %s", len(signatures), workspace_path)
    return signatures


def _find_tsx_files(workspace_path: str) -> list[str]:
    results: list[str] = []
    for root, _dirs, files in os.walk(workspace_path):
        for f in files:
            if f.endswith(".tsx"):
                results.append(os.path.join(root, f))
    return results


def _extract_balanced_block(content: str, start_pattern: str) -> str | None:
    match = re.search(start_pattern, content, re.DOTALL)
    if not match:
        return None
    start = match.start()
    body_start = content.index("{", match.end() - 1)
    depth = 0
    i = body_start
    for i in range(body_start, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
    return None


def _find_props_type_name(content: str) -> str | None:
    match = re.search(r'\)\s*:\s*(\w+)', content)
    if match:
        return match.group(1)
    return None


def _extract_single_file(filepath: str) -> dict[str, Any] | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.warning("Cannot read %s: %s", filepath, str(e))
        return None

    imports = _IMPORT_PATTERN.findall(content)
    imports = [line.strip() for line in imports if line.strip()]

    # Collect ALL interface/type blocks (not just Props-ending)
    all_blocks: list[tuple[str, str]] = []
    for m in _ALL_TYPE_PATTERN.finditer(content):
        block = _extract_balanced_block(content, rf'\b{m.group(1)}\s+{m.group(2)}')
        if block:
            all_blocks.append((m.group(2), block.strip()))

    if not all_blocks:
        return None

    # Find the props interface for this component
    component_match = _COMPONENT_EXPORT_PATTERN.search(content)
    if not component_match:
        return None

    component_name = component_match.group(1)
    props_type_name = _find_props_type_name(content[component_match.start():])

    props_block = None
    if props_type_name:
        for name, block in all_blocks:
            if name == props_type_name:
                props_block = block
                break

    if not props_block:
        for name, block in all_blocks:
            if name.endswith("Props") or name == "Props":
                props_block = block
                break

    if not props_block:
        # Fall back to the first type block
        props_block = all_blocks[0][1]

    # Collect extra types (everything except the props block)
    extra_types: list[str] = []
    for name, block in all_blocks:
        if block != props_block:
            extra_types.append(block)

    # Extract prop names + distinguish required vs optional from the interface block
    required_props: list[str] = []
    optional_props: list[str] = []
    for m in _PROP_DETAIL_PATTERN.finditer(props_block):
        name, is_optional = m.group(1), m.group(2)
        if is_optional == '?':
            optional_props.append(name)
        else:
            required_props.append(name)
    prop_names: list[str] = required_props + optional_props

    # Fase 2.5: SIGNATURE_EMPTY warning — detect extractor regressions early
    if component_name and not prop_names and ("Props" in props_block or "Props" in component_name):
        logger.warning(
            "SIGNATURE_EMPTY component=%s file=%s interface_found=%s props_extracted=[]",
            component_name,
            filepath,
            props_type_name or "Props",
        )

    return {
        "component_name": component_name,
        "props": props_block.strip(),
        "extra_types": extra_types,
        "prop_names": prop_names,
        "required_props": required_props,
        "optional_props": optional_props,
        "imports": imports,
        "file_path": filepath,
    }


def get_signature_metrics(signatures: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Fase 2.4: Generate structured signature metrics report.

    Returns:
        dict with total_components, parsed_components,
        empty_signatures, components_with_required_props
    """
    total = len(signatures)
    with_props = sum(1 for s in signatures.values() if s.get("prop_names"))
    empty_sigs = sum(1 for s in signatures.values() if not s.get("prop_names"))
    with_required = sum(1 for s in signatures.values() if s.get("required_props"))

    return {
        "total_components": total,
        "parsed_components": with_props,
        "empty_signatures": empty_sigs,
        "components_with_required_props": with_required,
    }
