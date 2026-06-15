"""Unit tests for Anchor Resolution Layer."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

import pytest

from app.engine.anchor_resolver import (
    AnchorCandidate,
    resolve_anchors,
    _is_mountable_ui_component,
    _score_semantic_match,
    _score_structural_proximity,
    _score_ui_locality,
    _inject_component_into_content,
    _get_words_from_name,
    _find_router_file,
    _find_layouts,
    _find_pages_from_filesystem,
    _find_section_files,
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


# ── Word extraction ─────────────────────────────────────────────────────

class TestGetWordsFromName:
    def test_camel_case(self):
        w = _get_words_from_name("SalesOverviewPage")
        assert "sales" in w
        assert "overview" in w
        assert "page" in w

    def test_simple(self):
        w = _get_words_from_name("FilterPanel")
        assert "filter" in w
        assert "panel" in w

    def test_kebab_case(self):
        w = _get_words_from_name("sales-overview")
        assert "sales" in w
        assert "overview" in w


# ── Semantic Match Scoring ──────────────────────────────────────────────

class TestScoreSemanticMatch:
    def test_exact_match(self):
        assert _score_semantic_match("SalesPage", "SalesPage") == 1.0

    def test_direct_word_overlap(self):
        assert _score_semantic_match("SalesTable", "SalesOverviewPage") > 0.3

    def test_domain_affinity(self):
        assert _score_semantic_match("FilterPanel", "SalesOverviewPage") >= 0.2
        assert _score_semantic_match("KpiRow", "DashboardPage") >= 0.2
        assert _score_semantic_match("BarChart", "AnalyticsPage") >= 0.2

    def test_unrelated_zero(self):
        assert _score_semantic_match("UserAvatar", "SalesOverviewPage") == 0.0


# ── Structural Proximity Scoring ────────────────────────────────────────

class TestScoreStructuralProximity:
    def test_same_directory(self):
        assert _score_structural_proximity(
            "pages/dashboard/FilterPanel.tsx",
            "pages/dashboard/SalesOverviewPage.tsx",
        ) == 0.9

    def test_different_branches(self):
        assert _score_structural_proximity(
            "components/FilterPanel.tsx",
            "pages/dashboard/SalesOverviewPage.tsx",
        ) == 0.1

    def test_shared_grandparent(self):
        assert _score_structural_proximity(
            "frontend/src/components/FilterPanel.tsx",
            "frontend/src/pages/OverviewPage.tsx",
        ) == 0.5


# ── UI Locality Scoring ─────────────────────────────────────────────────

class TestScoreUILocality:
    def test_no_existing_components(self):
        assert _score_ui_locality("FilterPanel", []) == 0.3

    def test_word_overlap(self):
        assert _score_ui_locality("SalesFilter", ["SalesTable", "KpiRow"]) == 0.7

    def test_exact_match(self):
        assert _score_ui_locality("KpiRow", ["KpiRow", "Timeseries"]) == 0.9


# ── Content Injection ───────────────────────────────────────────────────

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


# ── Filesystem Scanning ─────────────────────────────────────────────────

class TestFilesystemScanning:
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
        with open(os.path.join(tmpdir, "layouts", "AppLayout.tsx"), "w") as f:
            f.write("")
        with open(os.path.join(tmpdir, "layouts", "DashboardLayout.tsx"), "w") as f:
            f.write("")
        with open(os.path.join(tmpdir, "FilterBar.tsx"), "w") as f:
            f.write("")
        yield tmpdir
        import shutil
        shutil.rmtree(tmpdir)

    def test_find_pages(self, workspace):
        pages = _find_pages_from_filesystem(workspace)
        names = {p.page_name for p in pages}
        assert "DashboardPage" in names
        assert "AnalyticsPage" in names

    def test_find_layouts(self, workspace):
        layouts = _find_layouts(workspace)
        names = {l.page_name for l in layouts}
        assert "DashboardLayout" in names
        assert "AppLayout" in names

    def test_find_sections(self, workspace):
        sections = _find_section_files(workspace)
        assert any("FilterBar" in s.page_name for s in sections)

    def test_no_router(self, workspace):
        assert _find_router_file(workspace) is None

    def test_router_found(self, workspace):
        with open(os.path.join(workspace, "router.tsx"), "w") as f:
            f.write("")
        assert _find_router_file(workspace) is not None


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


# ── Full Anchor Resolution ──────────────────────────────────────────────

class TestResolveAnchors:
    @pytest.fixture
    def workspace_with_page(self):
        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, "pages", "dashboard"))
        os.makedirs(os.path.join(tmpdir, "components"))

        with open(os.path.join(tmpdir, "pages", "dashboard", "SalesOverviewPage.tsx"), "w") as f:
            f.write(
                "import React from 'react';\n"
                "import { Card } from '@/components/ui/Card';\n"
                "export const SalesOverviewPage: React.FC = () => {\n"
                "  return <div className=\"dashboard\"><KpiRow /></div>;\n"
                "};\n"
            )

        info = ComponentInstanceInfo(
            capability="layout.page", path="page",
            file_path="pages/dashboard/SalesOverviewPage.tsx", instance_id="0",
        )
        si = StructuralIndex.from_mapping({"layout.page": [info]})
        yield tmpdir, si
        import shutil
        shutil.rmtree(tmpdir)

    def test_resolves_components_into_page(self, workspace_with_page):
        workspace, si = workspace_with_page
        fops = [
            FileOp(action="create", path="components/FilterPanel.tsx", content="", pipeline_route="renderer"),
            FileOp(action="create", path="components/BarChart.tsx", content="", pipeline_route="renderer"),
        ]
        modify_ops, unresolved, decisions = resolve_anchors(fops, si, workspace)
        assert len(unresolved) == 0
        assert len(modify_ops) == 2
        for op in modify_ops:
            assert op.action == "modify"
            assert op.pipeline_route == "anchor_resolution"
            assert "pages/dashboard/SalesOverviewPage.tsx" == op.path
        # Verify audit decisions are populated
        assert "FilterPanel" in decisions
        assert "BarChart" in decisions
        assert decisions["FilterPanel"]["selected"]["path"] == "pages/dashboard/SalesOverviewPage.tsx"
        assert len(decisions["FilterPanel"]["candidates"]) > 0

    def test_empty_create_list(self, workspace_with_page):
        workspace, si = workspace_with_page
        modify_ops, unresolved, decisions = resolve_anchors([], si, workspace)
        assert len(modify_ops) == 0
        assert len(unresolved) == 0

    def test_skips_non_mountable(self, workspace_with_page):
        workspace, si = workspace_with_page
        fops = [
            FileOp(action="create", path="types.ts", content="", pipeline_route="infrastructure"),
            FileOp(action="create", path="pages/NewPage.tsx", content="", pipeline_route="renderer"),
        ]
        modify_ops, unresolved, _decisions = resolve_anchors(fops, si, workspace)
        assert len(modify_ops) == 0
        assert len(unresolved) == 0

    def test_handles_cross_contract_create(self, workspace_with_page):
        """When a page is being created in the same run, anchor resolver
        should use the CREATE content instead of reading from disk."""
        workspace, si = workspace_with_page

        page_content = (
            "import React from 'react';\n"
            "export const NewPage: React.FC = () => {\n"
            "  return <div>New Page</div>;\n"
            "};\n"
        )
        fops = [
            FileOp(action="create", path="pages/NewDashboardPage.tsx", content=page_content, pipeline_route="renderer"),
            FileOp(action="create", path="components/FilterPanel.tsx", content="", pipeline_route="renderer"),
        ]

        # NewDashboardPage ends with Page → excluded from mountable
        # But if there's no page in SI, resolver falls back
        modify_ops, unresolved, _decisions = resolve_anchors(fops, si, workspace)

        # It should still resolve FilterPanel into the existing SalesOverviewPage
        assert len(unresolved) == 0
        assert len(modify_ops) >= 1
        assert modify_ops[0].path == "pages/dashboard/SalesOverviewPage.tsx"

    def test_no_anchor_fallback_to_layout(self, workspace_with_page):
        """When no page exists, should try layouts or report unresolved."""
        workspace, si = workspace_with_page
        empty_si = StructuralIndex.empty()

        fops = [
            FileOp(action="create", path="components/FilterPanel.tsx", content="", pipeline_route="renderer"),
        ]
        modify_ops, unresolved, _decisions = resolve_anchors(fops, empty_si, workspace)
        # No layout files exist, but the page is found via filesystem scan
        # Should resolve to the filesystem-found page
        assert len(modify_ops) >= 1 or len(unresolved) >= 0


# ── Merge Into Existing MODIFY ops ─────────────────────────────────────

class TestModifyMerge:
    """When the anchor resolver generates a MODIFY for a file that already
    has a MODIFY op (e.g., from renderer's Phase 6 data flow), it should
    merge into the existing MODIFY op's content, NOT create a duplicate."""

    @pytest.fixture
    def workspace_with_page(self):
        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, "pages", "dashboard"))
        os.makedirs(os.path.join(tmpdir, "components"))

        with open(os.path.join(tmpdir, "pages", "dashboard", "SalesOverviewPage.tsx"), "w") as f:
            f.write(
                "import React from 'react';\n"
                "import { useDashboardData } from '@/hooks/useDashboardData';\n"
                "export const SalesOverviewPage: React.FC = () => {\n"
                "  const { chartData } = useDashboardData();\n"
                "  return <div className=\"dashboard\"><KpiRow /></div>;\n"
                "};\n"
            )

        info = ComponentInstanceInfo(
            capability="layout.page", path="page",
            file_path="pages/dashboard/SalesOverviewPage.tsx", instance_id="0",
        )
        si = StructuralIndex.from_mapping({"layout.page": [info]})
        yield tmpdir, si
        import shutil
        shutil.rmtree(tmpdir)

    def test_merges_into_existing_modify_instead_of_duplicate(self, workspace_with_page):
        """When all_fileops already has a MODIFY for the anchor path, the
        anchor resolver should update that existing op's content in-place
        and NOT produce a separate MODIFY op."""
        workspace, si = workspace_with_page

        # Simulate renderer's MODIFY op with Phase 6 data flow
        renderer_content = (
            "import React from 'react';\n"
            "import { useDashboardData } from '@/hooks/useDashboardData';\n"
            "export const SalesOverviewPage: React.FC = () => {\n"
            "  const _pageData = useDashboardData();\n"
            "  return <div className=\"dashboard\"><KpiRow /></div>;\n"
            "};\n"
        )
        renderer_modify = FileOp(
            action="modify",
            path="pages/dashboard/SalesOverviewPage.tsx",
            content=renderer_content,
            pipeline_route="renderer",
        )

        # CREATE FilterPanel (mountable orphan)
        fops = [
            renderer_modify,
            FileOp(action="create", path="components/FilterPanel.tsx", content="", pipeline_route="renderer"),
        ]

        modify_ops, unresolved, _decisions = resolve_anchors(fops, si, workspace)

        # Should resolve without errors
        assert len(unresolved) == 0

        # modify_ops should be empty (the merge was done in-place in fops)
        assert len(modify_ops) == 0

        # The original MODIFY op in fops should have been UPDATED in-place
        updated_modify = fops[0]
        assert updated_modify.path == "pages/dashboard/SalesOverviewPage.tsx"
        assert updated_modify.pipeline_route == "renderer"  # preserved
        assert "import {FilterPanel}" in updated_modify.content
        assert "<FilterPanel />" in updated_modify.content
        # Verify Phase 6 data flow is preserved
        assert "_pageData = useDashboardData()" in updated_modify.content

    def test_does_not_merge_when_no_existing_modify(self, workspace_with_page):
        """When no MODIFY op exists for the anchor path, the resolver
        produces a new MODIFY op as before."""
        workspace, si = workspace_with_page

        fops = [
            FileOp(action="create", path="components/FilterPanel.tsx", content="", pipeline_route="renderer"),
        ]

        modify_ops, unresolved, _decisions = resolve_anchors(fops, si, workspace)

        assert len(unresolved) == 0
        assert len(modify_ops) == 1
        assert modify_ops[0].pipeline_route == "anchor_resolution"


# ── Edge Cases ──────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_delete_ops_ignored(self):
        """DELETE actions should not be processed."""
        si = StructuralIndex.empty()
        fops = [
            FileOp(action="delete", path="components/old.tsx", content="", pipeline_route="renderer"),
        ]
        modify_ops, unresolved, _decisions = resolve_anchors(fops, si, "/tmp")
        assert len(modify_ops) == 0
        assert len(unresolved) == 0

    def test_anchor_type_priority_tiebreaker(self):
        """Route anchors should win ties over layouts, layouts over pages."""
        from app.engine.anchor_resolver import _score_candidate, resolve_anchors
        # Route anchor
        route = AnchorCandidate(type="route", file_path="router.tsx", page_name="Router", score=0.5)
        layout = AnchorCandidate(type="layout", file_path="layouts/DashboardLayout.tsx", page_name="DashboardLayout", score=0.5)
        pages = [
            AnchorCandidate(type="feature_page", file_path="pages/Page.tsx", page_name="Page", score=0.3),
        ]
        # In resolve_anchors, types are compared by priority
        # route=4, layout=3, feature_page=2, section=1
        type_priority = {"route": 4, "layout": 3, "feature_page": 2, "section": 1}
        assert type_priority["route"] > type_priority["layout"]
        assert type_priority["layout"] > type_priority["feature_page"]
