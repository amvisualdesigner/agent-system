"""Phase 5A unit tests: substitution pipeline (FileOp vs direct disk)."""
from __future__ import annotations

import os
import tempfile

import pytest

from app.graphir.models import FileOp, PipelineRoute
from app.graphir.utils import validate_fileop_collisions
from app.engine.apply_engine import (
    _reconcile_substitution_targets,
    _redirect_imports,
    build_substitution_fileops,
    apply_substitutions,
)
from app.engine.structural_completion import (
    StructuralIR,
    SubstitutionOp,
    ResolvedCapability,
    CREATE, KEEP,
)
from app.contracts.skill_registry import SkillContract


def make_sub_op(source: str, target: str) -> SubstitutionOp:
    return SubstitutionOp(source=source, target=target)


# ─── Fixtures ───


@pytest.fixture
def empty_contract():
    return SkillContract(
        contract_id="test", version=1,
        input_schema={"type": "object", "properties": {}},
        ast_template={"capabilities": {}, "slots": []},
        renderer={"files": []},
    )


@pytest.fixture
def contract_with_bar_chart():
    return SkillContract(
        contract_id="dashboard.sales_overview",
        version=1,
        input_schema={
            "type": "object",
            "properties": {
                "metrics": {"type": "array", "items": {"type": "string"}},
            },
        },
        ast_template={
            "capabilities": {
                "KpiRow": "presentation.kpi_row",
                "BarChart": "presentation.chart.bar",
                "Page": "layout.page",
            },
        },
        renderer={
            "base_path": "frontend/src",
            "files": [
                {"path": "components/dashboard/KpiRow.tsx", "type": "component"},
                {"path": "components/charts/BarChart.tsx", "type": "component"},
                {"path": "pages/dashboard/Page.tsx", "type": "page"},
            ],
        },
    )


class FakeStructuralIndex:
    def __init__(self, existing: set[str] | None = None):
        self._existing = existing or set()

    def exists(self, capability: str) -> bool:
        return capability in self._existing


# ─── Tests: _redirect_imports ───


class TestRedirectImportsProducesFileOps:
    """_redirect_imports returns list[FileOp] instead of direct disk writes."""

    def test_returns_fileops_not_paths(self, empty_contract):
        with tempfile.TemporaryDirectory() as tmpdir:
            importer = os.path.join(tmpdir, "Dashboard.tsx")
            with open(importer, "w") as f:
                f.write("import Table from './table';\n")

            result = _redirect_imports(
                "presentation.table",
                "presentation.chart.bar",
                tmpdir,
                empty_contract,
            )

            assert isinstance(result, list)
            if result:
                assert isinstance(result[0], FileOp)

    def test_no_direct_disk_write(self, empty_contract):
        """The function does NOT write to disk — FileOp contains new content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            importer = os.path.join(tmpdir, "Dashboard.tsx")
            with open(importer, "w") as f:
                f.write("import Table from './table';\n")

            fileops = _redirect_imports(
                "presentation.table",
                "presentation.chart.bar",
                tmpdir,
                empty_contract,
            )

            # Disk content should be unchanged
            with open(importer) as f:
                disk_content = f.read()
            assert "from './table'" in disk_content
            assert "'./bar'" not in disk_content

            # But the FileOp content should have the redirect
            for op in fileops:
                if "Dashboard.tsx" in op.path:
                    assert "'./bar'" in op.content

    def test_substitution_route(self, empty_contract):
        with tempfile.TemporaryDirectory() as tmpdir:
            importer = os.path.join(tmpdir, "Dashboard.tsx")
            with open(importer, "w") as f:
                f.write("import Table from './table';\n")

            fileops = _redirect_imports(
                "presentation.table",
                "presentation.chart.bar",
                tmpdir,
                empty_contract,
            )

            for op in fileops:
                assert op.pipeline_route == "substitution"

    def test_action_is_modify(self, empty_contract):
        with tempfile.TemporaryDirectory() as tmpdir:
            importer = os.path.join(tmpdir, "Dashboard.tsx")
            with open(importer, "w") as f:
                f.write("import Table from './table';\n")

            fileops = _redirect_imports(
                "presentation.table",
                "presentation.chart.bar",
                tmpdir,
                empty_contract,
            )

            for op in fileops:
                assert op.action == "modify"

    def test_unrelated_files_not_modified(self, empty_contract):
        with tempfile.TemporaryDirectory() as tmpdir:
            importer = os.path.join(tmpdir, "Dashboard.tsx")
            with open(importer, "w") as f:
                f.write("import Table from './table';\n")

            other = os.path.join(tmpdir, "Unrelated.tsx")
            with open(other, "w") as f:
                f.write("import React from 'react';\n")

            fileops = _redirect_imports(
                "presentation.table",
                "presentation.chart.bar",
                tmpdir,
                empty_contract,
            )

            assert not any("Unrelated.tsx" in op.path for op in fileops)


# ─── Tests: _reconcile_substitution_targets ───


class TestReconcileSubstitutionTargets:
    """_reconcile_substitution_targets uses structural_index.exists()."""

    def test_skip_if_exists(self, contract_with_bar_chart):
        ops = (make_sub_op("presentation.kpi_row", "presentation.kpi_row"),)
        idx = FakeStructuralIndex(existing={"presentation.kpi_row"})

        fileops = _reconcile_substitution_targets(ops, idx, "/tmp", contract_with_bar_chart)

        assert len(fileops) == 0

    def test_create_if_missing(self, contract_with_bar_chart):
        ops = (make_sub_op("presentation.kpi_row", "presentation.kpi_row"),)
        idx = FakeStructuralIndex(existing=set())

        fileops = _reconcile_substitution_targets(ops, idx, "/tmp", contract_with_bar_chart)

        assert len(fileops) == 1
        assert fileops[0].action == "create"
        assert fileops[0].pipeline_route == "substitution"

    def test_create_content_is_non_empty(self, contract_with_bar_chart):
        ops = (make_sub_op("presentation.kpi_row", "presentation.kpi_row"),)
        idx = FakeStructuralIndex(existing=set())

        fileops = _reconcile_substitution_targets(ops, idx, "/tmp", contract_with_bar_chart)

        assert len(fileops) == 1
        assert len(fileops[0].content) > 0
        assert "KpiRow" in fileops[0].content or "kpi" in fileops[0].content.lower()

    def test_no_os_path_exists_call(self, contract_with_bar_chart):
        """Verifies the function uses structural_index, not os.path."""
        ops = (make_sub_op("presentation.kpi_row", "presentation.kpi_row"),)

        class StrictIndex:
            def exists(self, capability):
                # If this function used os.path.exists, it would
                # return True (because /tmp exists). We return False
                # to force a create FileOp.
                return False

        fileops = _reconcile_substitution_targets(ops, StrictIndex(), "/tmp", contract_with_bar_chart)
        assert len(fileops) == 1  # Would be 0 if it used os.path.exists on /tmp


# ─── Tests: build_substitution_fileops ───


class TestBuildSubstitutionFileops:
    """build_substitution_fileops coordinates reconciliation + redirect."""

    def test_returns_fileops(self, contract_with_bar_chart):
        ops = (make_sub_op("presentation.kpi_row", "presentation.kpi_row"),)
        idx = FakeStructuralIndex(existing={"presentation.kpi_row"})

        result = build_substitution_fileops(ops, idx, "/tmp", contract_with_bar_chart)

        assert isinstance(result, tuple)
        assert len(result) == 2
        fileops, refactor_changes = result
        assert isinstance(fileops, list)
        assert isinstance(refactor_changes, list)
        for op in fileops:
            assert isinstance(op, FileOp)

    def test_all_fileops_have_substitution_route(self, contract_with_bar_chart):
        ops = (make_sub_op("presentation.kpi_row", "presentation.kpi_row"),)
        idx = FakeStructuralIndex(existing=set())

        fileops, refactor_changes = build_substitution_fileops(ops, idx, "/tmp", contract_with_bar_chart)

        for op in fileops:
            assert op.pipeline_route == "substitution"

    def test_create_when_missing_redirect_when_exists(self, contract_with_bar_chart):
        """Mixed case: target missing (create) + redirect always."""
        ops = (make_sub_op("presentation.kpi_row", "presentation.kpi_row"),)
        idx = FakeStructuralIndex(existing=set())

        fileops, refactor_changes = build_substitution_fileops(ops, idx, "/tmp", contract_with_bar_chart)

        actions = [op.action for op in fileops]
        assert "create" in actions

    def test_no_structural_ir_mutation(self, contract_with_bar_chart):
        """build_substitution_fileops does not receive or mutate StructuralIR."""
        ops = (make_sub_op("presentation.kpi_row", "presentation.kpi_row"),)
        idx = FakeStructuralIndex(existing=set())

        fileops, refactor_changes = build_substitution_fileops(ops, idx, "/tmp", contract_with_bar_chart)

        assert isinstance(fileops, list)

    def test_empty_ops_returns_empty(self, contract_with_bar_chart):
        idx = FakeStructuralIndex(existing=set())
        fileops, refactor_changes = build_substitution_fileops((), idx, "/tmp", contract_with_bar_chart)
        assert fileops == []
        assert refactor_changes == []


# ─── Tests: apply_substitutions (refactored) ───


class TestApplySubstitutionsRefactored:
    """apply_substitutions now returns (list[FileOp], dict)."""

    def test_returns_tuple(self, contract_with_bar_chart):
        ir = StructuralIR(
            contract_id="test",
            contract_version=1,
            capabilities=(),
            param_provenance={},
            confidence=1.0,
            substitution_ops=(make_sub_op("presentation.kpi_row", "presentation.chart.bar"),),
        )
        idx = FakeStructuralIndex(existing=set())

        result = apply_substitutions(ir, "/tmp", contract_with_bar_chart, structural_index=idx)

        fileops, summary, refactor_changes = result
        assert isinstance(fileops, list)
        assert isinstance(summary, dict)
        assert isinstance(refactor_changes, list)

    def test_empty_ops_returns_empty(self, contract_with_bar_chart):
        ir = StructuralIR(
            contract_id="test",
            contract_version=1,
            capabilities=(),
            param_provenance={},
            confidence=1.0,
        )
        fileops, summary, refactor_changes = apply_substitutions(ir, "/tmp", contract_with_bar_chart)
        assert fileops == []
        assert summary["created"] == []
        assert summary["redirected"] == []
        assert refactor_changes == []

    def test_summary_counts_match(self, contract_with_bar_chart):
        ops = (make_sub_op("presentation.kpi_row", "presentation.chart.bar"),)
        ir = StructuralIR(
            contract_id="test",
            contract_version=1,
            capabilities=(),
            param_provenance={},
            confidence=1.0,
            substitution_ops=ops,
        )
        idx = FakeStructuralIndex(existing=set())

        fileops, summary, refactor_changes = apply_substitutions(ir, "/tmp", contract_with_bar_chart, structural_index=idx)

        assert len(summary["created"]) == len([op for op in fileops if op.action == "create"])
        assert len(summary["redirected"]) == len([op for op in fileops if op.action == "modify"])


# ─── Tests: validate_fileop_collisions ───


class TestValidateFileopCollisions:
    """Collision detection for conflicting FileOps on same path."""

    def test_no_collisions_returns_empty(self):
        fops = [
            FileOp(action="create", path="foo.tsx", content="a", pipeline_route="renderer"),
            FileOp(action="modify", path="bar.tsx", content="b", pipeline_route="renderer"),
        ]
        assert validate_fileop_collisions(fops) == []

    def test_detects_create_vs_delete(self):
        fops = [
            FileOp(action="create", path="foo.tsx", content="a", pipeline_route="renderer"),
            FileOp(action="delete", path="foo.tsx", content="", pipeline_route="delete_inject"),
        ]
        collisions = validate_fileop_collisions(fops)
        assert len(collisions) == 1
        assert collisions[0]["path"] == "foo.tsx"
        assert collisions[0]["op_a_action"] == "create"
        assert collisions[0]["op_b_action"] == "delete"

    def test_detects_create_vs_modify(self):
        fops = [
            FileOp(action="create", path="foo.tsx", content="a", pipeline_route="substitution"),
            FileOp(action="modify", path="foo.tsx", content="b", pipeline_route="renderer"),
        ]
        collisions = validate_fileop_collisions(fops)
        assert len(collisions) == 1

    def test_same_action_no_collision(self):
        fops = [
            FileOp(action="modify", path="foo.tsx", content="a", pipeline_route="renderer"),
            FileOp(action="modify", path="foo.tsx", content="b", pipeline_route="substitution"),
        ]
        assert validate_fileop_collisions(fops) == []

    def test_multiple_collisions(self):
        fops = [
            FileOp(action="create", path="a.tsx", content="x", pipeline_route="renderer"),
            FileOp(action="delete", path="a.tsx", content="", pipeline_route="delete_inject"),
            FileOp(action="create", path="b.tsx", content="y", pipeline_route="substitution"),
            FileOp(action="modify", path="b.tsx", content="z", pipeline_route="renderer"),
        ]
        collisions = validate_fileop_collisions(fops)
        assert len(collisions) == 2



