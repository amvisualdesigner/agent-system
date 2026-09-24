"""Unit tests for the F1/C4 Anchor Resolution Layer.

F1/C4 invariants locked here:
  - The anchor decision is STRICTLY PHYSICAL, never semantic:
      forced (plan specified) → use it
      0 candidates     → CONFLICT
      1 candidate      → use it
      N candidates     → CONFLICT unless the plan specified one (forced)
  - No scoring, no domain affinities, no word overlap, no type fallback.
  - A component already composed by the renderer → composed_skip (no
    double mount, no spurious unresolved warning).
  - Anchor MODIFY ops merge into existing CREATE/MODIFY ops for the same
    path instead of producing a collision.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from app.engine.anchor_resolver import (
    AnchorCandidate,
    resolve_anchors,
    _is_mountable_ui_component,
    _capability_for_path,
    _content_imports,
    _inject_component_into_content,
    _find_pages_from_filesystem,
    _find_pages_from_index,
)
from app.graphir.models import FileOp
from app.engine.structural_index import StructuralIndex
from app.engine.state_adapter import ComponentInstanceInfo


# ── _is_mountable_ui_component ──────────────────────────────────────────

class TestIsMountableUIComponent:
    def test_infrastructure_excluded(self):
        fop = FileOp(action="create", path="types.ts", content="", pipeline_route="infrastructure")
        assert not _is_mountable_ui_component(fop)

    def test_substitution_excluded(self):
        fop = FileOp(action="create", path="comp.tsx", content="", pipeline_route="substitution")
        assert not _is_mountable_ui_component(fop)

    def test_non_tsx_excluded(self):
        fop = FileOp(action="create", path="comp.ts", content="", pipeline_route="renderer")
        assert not _is_mountable_ui_component(fop)

    def test_page_excluded(self):
        fop = FileOp(action="create", path="pages/SalesPage.tsx", content="", pipeline_route="renderer")
        assert not _is_mountable_ui_component(fop)

    def test_layout_excluded(self):
        fop = FileOp(action="create", path="layouts/DashboardLayout.tsx", content="", pipeline_route="renderer")
        assert not _is_mountable_ui_component(fop)

    def test_filter_panel_included(self):
        fop = FileOp(action="create", path="components/FilterPanel.tsx", content="", pipeline_route="renderer")
        assert _is_mountable_ui_component(fop)

    def test_kpi_row_included(self):
        fop = FileOp(action="create", path="components/KpiRow.tsx", content="", pipeline_route="renderer")
        assert _is_mountable_ui_component(fop)


# ── Content Injection (pure, unchanged) ─────────────────────────────────

class TestInjectComponentIntoContent:
    def test_injects_import_and_jsx(self):
        content = (
            "import React from 'react';\n"
            "import { Card } from '@/components/ui/Card';\n"
            "\n"
            "export const Page: React.FC = () => {\n"
            "  return (\n"
            "    <div>\n"
            "      <KpiRow />\n"
            "    </div>\n"
            "  );\n"
            "};\n"
        )
        result = _inject_component_into_content(
            content, "FilterPanel", "components/FilterPanel.tsx", ".",
        )
        assert result is not None
        assert "import {FilterPanel}" in result
        assert "<FilterPanel />" in result

    def test_no_duplicate_injection(self):
        content = (
            "import React from 'react';\n"
            "import { FilterPanel } from './components/FilterPanel';\n"
            "\n"
            "export const Page: React.FC = () => {\n"
            "  return (\n"
            "    <div>\n"
            "      <FilterPanel />\n"
            "    </div>\n"
            "  );\n"
            "};\n"
        )
        result = _inject_component_into_content(
            content, "FilterPanel", "components/FilterPanel.tsx", ".",
        )
        assert result is None

    def test_no_imports_fails(self):
        content = "const x = 1;\n"
        result = _inject_component_into_content(
            content, "FilterPanel", "components/FilterPanel.tsx", ".",
        )
        assert result is None


class TestContentImports:
    def test_detects_import(self):
        assert _content_imports(
            "import React from 'react';\nimport { KpiRow } from './KpiRow';\n", "KpiRow",
        )
        assert _content_imports("import KpiRow from './KpiRow';\n", "KpiRow")

    def test_missing_import(self):
        assert not _content_imports("import React from 'react';\n", "KpiRow")


# ── Filesystem Page Scan (physical page containers) ─────────────────────

class TestFilesystemPageScan:
    @pytest.fixture
    def workspace(self):
        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, "pages"))
        os.makedirs(os.path.join(tmpdir, "layouts"))
        os.makedirs(os.path.join(tmpdir, "components"))

        with open(os.path.join(tmpdir, "pages", "DashboardPage.tsx"), "w") as f:
            f.write("")
        with open(os.path.join(tmpdir, "pages", "AnalyticsPage.tsx"), "w") as f:
            f.write("")
        # Infra shells must NOT be candidates — mounting into them is a
        # routing/rendering decision (semantic), out of C4 scope.
        with open(os.path.join(tmpdir, "layouts", "AppLayout.tsx"), "w") as f:
            f.write("")
        with open(os.path.join(tmpdir, "layouts", "DashboardLayout.tsx"), "w") as f:
            f.write("")
        with open(os.path.join(tmpdir, "FilterBar.tsx"), "w") as f:
            f.write("")
        yield tmpdir
        shutil.rmtree(tmpdir)

    def test_find_pages(self, workspace):
        pages = _find_pages_from_filesystem(workspace)
        names = {p.page_name for p in pages}
        assert "DashboardPage" in names
        assert "AnalyticsPage" in names
        # Non-page files (layouts, sections) are never page anchors
        assert not any("Layout" in p.page_name for p in pages)
        assert not any(p.page_name == "FilterBar" for p in pages)

    def test_capability_for_path_fallback(self):
        si = StructuralIndex.empty()
        # Not registered in the index → deterministic name-map fallback
        assert _capability_for_path(si, "components/KpiRow.tsx") == "presentation.kpi_row"
        # Fully unknown component names resolve to nothing → orphan matrix
        assert _capability_for_path(si, "pages/SalesPage.tsx") is None


# ── Pages from StructuralIndex ──────────────────────────────────────────

class TestFindPagesFromIndex:
    def test_finds_page_from_layout_capability(self):
        info = ComponentInstanceInfo(
            capability="layout.page", path="page",
            file_path="pages/dashboard/SalesOverviewPage.tsx", instance_id="0",
        )
        si = StructuralIndex.from_mapping({"layout.page": [info]})
        pages = _find_pages_from_index(si)
        assert len(pages) == 1
        assert pages[0].page_name == "SalesOverviewPage"


def _mk_workspace(files: dict[str, str]) -> str:
    tmpdir = tempfile.mkdtemp()
    for rel, content in files.items():
        abs_path = os.path.join(tmpdir, rel)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as f:
            f.write(content)
    return tmpdir


RICH_PAGE = (
    "import React from 'react';\n"
    "import { Card } from '@/components/ui/Card';\n"
    "export const SalesOverviewPage: React.FC = () => {\n"
    "  return (\n"
    "    <div className=\"dashboard\">\n"
    "      <KpiRow />\n"
    "    </div>\n"
    "  );\n"
    "};\n"
)

COMPOSED_PAGE = (
    "import React from 'react';\n"
    "import { Card } from '@/components/ui/Card';\n"
    "import { KpiRow } from './components/KpiRow';\n"
    "export const SalesOverviewPage: React.FC = () => {\n"
    "  return (\n"
    "    <div className=\"dashboard\">\n"
    "      <KpiRow />\n"
    "    </div>\n"
    "  );\n"
    "};\n"
)


def _si_with_page(*paths: str) -> StructuralIndex:
    infos = [
        ComponentInstanceInfo(
            capability="layout.page", path="page",
            file_path=p, instance_id=str(i),
        )
        for i, p in enumerate(paths)
    ]
    return StructuralIndex.from_mapping({"layout.page": infos})


def _create(path: str, route: str = "renderer") -> FileOp:
    return FileOp(action="create", path=path, content="x", pipeline_route=route)


# ── F1/C4 Physical Decision Matrix ──────────────────────────────────────

class TestPhysicalMatrix:
    """forced → use; 0 → CONFLICT; 1 → use; N → CONFLICT (unless forced)."""

    def test_forced_path_wins_over_n_candidates(self):
        ws = _mk_workspace({
            "pages/One.tsx": RICH_PAGE,
            "pages/Two.tsx": RICH_PAGE,
        })
        si = _si_with_page("pages/One.tsx", "pages/Two.tsx")
        try:
            fops = [_create("components/FilterPanel.tsx")]
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(
                fops, si, ws, forced_anchor_path="pages/Two.tsx",
            )
            assert conflicts == []
            assert len(modify_ops) == 1
            assert modify_ops[0].path == "pages/Two.tsx"
            assert decisions["FilterPanel"]["decision"] == "forced"
            assert decisions["FilterPanel"]["selected"]["path"] == "pages/Two.tsx"
        finally:
            shutil.rmtree(ws)

    def test_n_candidates_conflict_without_forced(self):
        ws = _mk_workspace({
            "pages/One.tsx": RICH_PAGE,
            "pages/Two.tsx": RICH_PAGE,
        })
        si = _si_with_page("pages/One.tsx", "pages/Two.tsx")
        try:
            fops = [_create("components/FilterPanel.tsx")]
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(fops, si, ws)
            assert modify_ops == []
            assert len(conflicts) == 1
            assert "2 physical anchors" in conflicts[0]
            assert decisions["FilterPanel"]["decision"] == "conflict"
        finally:
            shutil.rmtree(ws)

    def test_zero_candidates_conflict(self):
        ws = _mk_workspace({})
        si = StructuralIndex.empty()
        try:
            fops = [_create("components/FilterPanel.tsx")]
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(fops, si, ws)
            assert modify_ops == []
            assert len(conflicts) == 1
            assert "0 physical anchors" in conflicts[0]
            assert decisions["FilterPanel"]["decision"] == "conflict"
        finally:
            shutil.rmtree(ws)

    def test_zero_candidates_conflict_even_with_composition_parent_missing(self):
        """Composition parent capability with no physical instance → CONFLICT."""
        ws = _mk_workspace({})
        si = StructuralIndex.empty()
        try:
            fops = [_create("components/KpiRow.tsx")]
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(
                fops, si, ws, composition_map={"presentation.kpi_row": "layout.page"},
            )
            assert modify_ops == []
            assert len(conflicts) == 1
            assert "0 physical anchors" in conflicts[0]
        finally:
            shutil.rmtree(ws)

    def test_single_candidate_is_used(self):
        ws = _mk_workspace({"pages/dashboard/SalesOverviewPage.tsx": RICH_PAGE})
        si = _si_with_page("pages/dashboard/SalesOverviewPage.tsx")
        try:
            fops = [_create("components/FilterPanel.tsx")]
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(fops, si, ws)
            assert conflicts == []
            assert len(modify_ops) == 1
            assert modify_ops[0].path == "pages/dashboard/SalesOverviewPage.tsx"
            assert "FilterPanel" in modify_ops[0].content
            assert decisions["FilterPanel"]["decision"] == "single"
        finally:
            shutil.rmtree(ws)

    def test_composition_child_single_parent_instance(self):
        """Contract child: the composition parent is the physical anchor."""
        ws = _mk_workspace({"pages/dashboard/Page.tsx": RICH_PAGE})
        si = _si_with_page("pages/dashboard/Page.tsx")
        try:
            fops = [_create("components/KpiRow.tsx")]
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(
                fops, si, ws, composition_map={"presentation.kpi_row": "layout.page"},
            )
            assert conflicts == []
            assert len(modify_ops) == 1
            assert modify_ops[0].path == "pages/dashboard/Page.tsx"
        finally:
            shutil.rmtree(ws)

    def test_composition_child_n_parent_instances_conflict(self):
        """Composition parent has 2 physical instances → CONFLICT + reason names them."""
        ws = _mk_workspace({
            "pages/dashboard/Page.tsx": RICH_PAGE,
            "pages/marketing/Page.tsx": RICH_PAGE,
        })
        si = _si_with_page("pages/dashboard/Page.tsx", "pages/marketing/Page.tsx")
        try:
            fops = [_create("components/KpiRow.tsx")]
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(
                fops, si, ws, composition_map={"presentation.kpi_row": "layout.page"},
            )
            assert modify_ops == []
            assert len(conflicts) == 1
            assert "2 physical anchors" in conflicts[0]
            # plan-forced overrides the ambiguity for contract children too
            mo2, _, _, c2 = resolve_anchors(
                fops, si, ws,
                forced_anchor_path="pages/marketing/Page.tsx",
                composition_map={"presentation.kpi_row": "layout.page"},
            )
            assert c2 == []
            assert len(mo2) == 1 and mo2[0].path == "pages/marketing/Page.tsx"
        finally:
            shutil.rmtree(ws)

    def test_composed_skip_no_spurious_unresolved(self):
        """A component already composed by the renderer is skipped (no double
        mount, no unresolved noise, no conflict) — F1/C4 fixes the pre-C4
        behavior that logged a spurious 'unresolved'."""
        ws = _mk_workspace({"pages/dashboard/Page.tsx": COMPOSED_PAGE})
        si = _si_with_page("pages/dashboard/Page.tsx")
        # Renderer regenerated the page and mounted KpiRow (import present)
        renderer_modify = FileOp(
            action="modify", path="pages/dashboard/Page.tsx",
            content=COMPOSED_PAGE, pipeline_route="renderer",
        )
        fops = [renderer_modify, _create("components/KpiRow.tsx")]
        try:
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(fops, si, ws)
            assert conflicts == []
            assert unresolved == []
            assert modify_ops == []
            assert decisions["KpiRow"]["decision"] == "composed_skip"
        finally:
            shutil.rmtree(ws)

    def test_injection_failure_is_conflict(self):
        """A single physical anchor with no usable mount point (self-closing
        root) is physically unmountable → CONFLICT, not a silent skip."""
        ws = _mk_workspace({
            "pages/dashboard/Page.tsx": (
                "import React from 'react';\n"
                "export const Page: React.FC = () => <div/>;\n"
            ),
        })
        si = _si_with_page("pages/dashboard/Page.tsx")
        try:
            fops = [_create("components/FilterPanel.tsx")]
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(fops, si, ws)
            assert modify_ops == []
            assert len(conflicts) == 1
            assert decisions["FilterPanel"]["decision"] == "conflict"
        finally:
            shutil.rmtree(ws)

    def test_empty_create_list(self):
        ws = _mk_workspace({"pages/dashboard/Page.tsx": RICH_PAGE})
        si = _si_with_page("pages/dashboard/Page.tsx")
        try:
            modify_ops, unresolved, decisions, conflicts = resolve_anchors([], si, ws)
            assert modify_ops == []
            assert unresolved == []
            assert conflicts == []
        finally:
            shutil.rmtree(ws)


# ── Merge Into Existing FileOps (no collisions) ─────────────────────────

class TestModifyMerge:
    def test_merges_into_existing_modify_instead_of_duplicate(self):
        ws = _mk_workspace({
            "pages/dashboard/SalesOverviewPage.tsx": RICH_PAGE,
        })
        si = _si_with_page("pages/dashboard/SalesOverviewPage.tsx")
        renderer_modify = FileOp(
            action="modify", path="pages/dashboard/SalesOverviewPage.tsx",
            content=RICH_PAGE, pipeline_route="renderer",
        )
        fops = [renderer_modify, _create("components/FilterPanel.tsx")]
        try:
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(fops, si, ws)
            assert conflicts == []
            assert len(modify_ops) == 0
            updated = fops[0]
            assert updated.pipeline_route == "renderer"  # preserved
            assert "import {FilterPanel}" in updated.content
            assert "<FilterPanel />" in updated.content
        finally:
            shutil.rmtree(ws)

    def test_does_not_merge_when_no_existing_fileop(self):
        ws = _mk_workspace({"pages/dashboard/SalesOverviewPage.tsx": RICH_PAGE})
        si = _si_with_page("pages/dashboard/SalesOverviewPage.tsx")
        fops = [_create("components/FilterPanel.tsx")]
        try:
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(fops, si, ws)
            assert conflicts == []
            assert len(modify_ops) == 1
            assert modify_ops[0].pipeline_route == "anchor_resolution"
        finally:
            shutil.rmtree(ws)

    def test_merges_into_existing_create_instead_of_collision(self):
        """If the anchor file IS being created this run and does not yet
        import the component, the import is merged into the CREATE op."""
        page_content = (
            "import React from 'react';\n"
            "export const NewPage: React.FC = () => {\n"
            "  return <div>New Page</div>;\n"
            "};\n"
        )
        ws = _mk_workspace({
            "pages/NewDashboardPage.tsx": page_content,
        })
        si = _si_with_page()
        page_create = FileOp(
            action="create", path="pages/NewDashboardPage.tsx",
            content=page_content, pipeline_route="renderer",
        )
        fops = [page_create, _create("components/FilterPanel.tsx")]
        try:
            modify_ops, unresolved, decisions, conflicts = resolve_anchors(
                fops, si, ws, forced_anchor_path="pages/NewDashboardPage.tsx",
            )
            assert conflicts == []
            assert len(modify_ops) == 0
            assert "FilterPanel" in fops[0].content
            assert fops[0].action == "create"
        finally:
            shutil.rmtree(ws)


# ── Provenance / audit-only decision record ─────────────────────────────

class TestDecisionAuditRecord:
    def test_decisions_carry_candidates_and_selected(self):
        ws = _mk_workspace({"pages/dashboard/SalesOverviewPage.tsx": RICH_PAGE})
        si = _si_with_page("pages/dashboard/SalesOverviewPage.tsx")
        fops = [_create("components/FilterPanel.tsx")]
        try:
            _modify_ops, _unresolved, decisions, conflicts = resolve_anchors(fops, si, ws)
            assert conflicts == []
            rec = decisions["FilterPanel"]
            assert rec["decision"] == "single"
            assert rec["selected"] == {
                "path": "pages/dashboard/SalesOverviewPage.tsx", "type": "feature_page",
            }
            assert rec["candidates"] == [
                {"path": "pages/dashboard/SalesOverviewPage.tsx", "type": "feature_page"},
            ]
        finally:
            shutil.rmtree(ws)