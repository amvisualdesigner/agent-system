"""Content quality assertions for container tests.

Checks that generated file content contains real implementation,
not empty stubs from signature override (react_backend.py:573).

Bug reference: tmp/reporte-phase5.md §1
  _render_signature() generates `_props` + empty div with __COMPOSITION__,
  short-circuiting the real template.
"""

from __future__ import annotations


BROKEN_PATTERNS = [
    "__COMPOSITION__",
]

BROKEN_PROP_PATTERNS = [
    "(_props)",  # unused props parameter
    "= (_props)",  # alternate spacing
]

REQUIRED_IMPORTS = [
    "import React",
]


def assert_content_quality(ops: list[dict], path_hint: str, *extra_markers: str):
    """Assert generated file content contains real implementation.

    Args:
        ops: operations from execution result
        path_hint: substring match on op['path'] (e.g. 'KpiRow', 'Timeseries')
        extra_markers: additional required substrings (e.g. 'data.map', '<polyline')
    """
    targets = [
        op for op in ops
        if op["action"] in ("create", "modify")
        and path_hint in op["path"]
    ]
    assert targets, (
        f"No create/modify op matching {path_hint!r} found in {[o['path'] for o in ops]}"
    )

    for op in targets:
        content = op.get("content", "")
        path = op["path"]

        # 1. Not empty
        assert len(content) > 50, (
            f"{path}: content too short ({len(content)} chars) — likely empty stub"
        )

        # 2. No broken template placeholders
        for pat in BROKEN_PATTERNS:
            assert pat not in content, (
                f"{path}: contains broken template placeholder {pat!r}"
            )

        # 3. Props are actually used (not _props)
        for pat in BROKEN_PROP_PATTERNS:
            assert pat not in content, (
                f"{path}: contains unused _props pattern {pat!r} — "
                "signature override generated stub instead of real implementation"
            )

        # 4. Has at least one import
        has_import = any(imp in content for imp in REQUIRED_IMPORTS)
        assert has_import, (
            f"{path}: missing React import — file may be empty or non-functional"
        )

        # 5. Has at least one JSX child element (not empty div)
        has_jsx_child = ">" in content and "<" in content.split(">", 1)[1] if ">" in content else False
        assert has_jsx_child, (
            f"{path}: no JSX children found — likely empty div stub"
        )

        # 6. Extra component-specific markers
        for marker in extra_markers:
            assert marker in content, (
                f"{path}: missing required marker {marker!r}. "
                "Signature override likely destroyed implementation."
            )


def assert_delete_operation(ops: list[dict], path_hint: str):
    """Assert a DELETE operation exists for the given path."""
    deletes = [
        op for op in ops
        if op["action"] == "delete"
        and path_hint in op["path"]
    ]
    assert deletes, (
        f"No delete op matching {path_hint!r} found in {[o['path'] for o in ops]}"
    )
