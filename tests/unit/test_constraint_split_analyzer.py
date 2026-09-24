"""SPLITAnalyzer — unit tests for structural split detection (ANALYSIS ONLY).

Pure Core: zero IO, 100% deterministic.
Tests that SPLITAnalyzer correctly detects overloaded files and produces a
RefactoringPlan recommendation. F5: the recommendation never reaches the
renderer — separation is locked in test_architecture_invariants and
test_constraint_line_range.
"""

from dataclasses import dataclass, field
from typing import Any

from app.graphir.constraint.split_analyzer import SPLITAnalyzer
from app.graphir.constraint.identity import CanonicalIdentity, build_identities
from app.graphir.constraint.models import (
    Decision,
    FileOpDecision,
    FileNode,
    RefactoringPlan,
    ConflictType,
    ResolutionStrategy,
    ConflictRecord,
)


def _file(path="src/Chart.tsx", comp_names=None) -> FileNode:
    return FileNode(
        id=f"file:{path}",
        node_type="file",
        path=path,
        exports=[],
        component_names=list(comp_names) if comp_names is not None else [],
    )


def _decision(
    node_id: str,
    decision: Decision = Decision.UPDATE,
    target: str = "src/Chart.tsx",
) -> FileOpDecision:
    return FileOpDecision(
        intent_id="test",
        graphir_node_id=node_id,
        decision=decision,
        target_file=target,
        confidence=0.8,
        rationale="test",
    )


def _identity(
    node_id: str,
    component: str = "KpiRow",
) -> CanonicalIdentity:
    return CanonicalIdentity(
        graphir_node_id=node_id,
        component_name=component,
        capability_id="presentation.kpi_row",
        params_hash="abc123",
        domain=("presentation",),
    )


class TestSPLITComponentCountThreshold:
    """Signal 1: file with >= N components + matching decision."""

    def test_triggers_split_at_threshold(self):
        sa = SPLITAnalyzer(threshold_component_count=3)
        decisions = {"n1": _decision("n1", Decision.UPDATE, "src/Chart.tsx")}
        identities = {"n1": _identity("n1", "KpiRow")}
        file_nodes = {
            "src/Chart.tsx": _file(
                "src/Chart.tsx",
                comp_names=["KpiRow", "Timeseries", "ChartHeader"],
            ),
        }
        plan = sa.analyze(decisions, identities, file_nodes)
        assert len(plan.splits) == 1
        s = plan.splits[0]
        assert s.source_file == "src/Chart.tsx"
        assert s.new_file == "src/components/KpiRow.tsx"
        assert s.components_to_extract == ["KpiRow"]

    def test_below_threshold_no_split(self):
        sa = SPLITAnalyzer(threshold_component_count=3)
        decisions = {"n1": _decision("n1", Decision.UPDATE, "src/Chart.tsx")}
        identities = {"n1": _identity("n1", "KpiRow")}
        file_nodes = {
            "src/Chart.tsx": _file(
                "src/Chart.tsx",
                comp_names=["KpiRow", "Timeseries"],
            ),
        }
        plan = sa.analyze(decisions, identities, file_nodes)
        assert len(plan.splits) == 0

    def test_no_identity_no_split(self):
        sa = SPLITAnalyzer(threshold_component_count=3)
        decisions = {"n1": _decision("n1", Decision.UPDATE, "src/Chart.tsx")}
        identities = {}
        file_nodes = {
            "src/Chart.tsx": _file(
                "src/Chart.tsx",
                comp_names=["KpiRow", "Timeseries", "ChartHeader"],
            ),
        }
        plan = sa.analyze(decisions, identities, file_nodes)
        assert len(plan.splits) == 0

    def test_component_not_in_file_no_split(self):
        sa = SPLITAnalyzer(threshold_component_count=3)
        decisions = {"n1": _decision("n1", Decision.UPDATE, "src/Chart.tsx")}
        identities = {"n1": _identity("n1", "DifferentComponent")}
        file_nodes = {
            "src/Chart.tsx": _file(
                "src/Chart.tsx",
                comp_names=["KpiRow", "Timeseries", "ChartHeader"],
            ),
        }
        plan = sa.analyze(decisions, identities, file_nodes)
        assert len(plan.splits) == 0

    def test_create_decisions_ignored(self):
        sa = SPLITAnalyzer(threshold_component_count=3)
        decisions = {"n1": _decision("n1", Decision.CREATE, "src/Chart.tsx")}
        identities = {"n1": _identity("n1", "KpiRow")}
        file_nodes = {
            "src/Chart.tsx": _file(
                "src/Chart.tsx",
                comp_names=["KpiRow", "Timeseries", "ChartHeader"],
            ),
        }
        plan = sa.analyze(decisions, identities, file_nodes)
        assert len(plan.splits) == 0

    def test_extend_also_triggers_split(self):
        sa = SPLITAnalyzer(threshold_component_count=3)
        decisions = {"n1": _decision("n1", Decision.EXTEND, "src/Chart.tsx")}
        identities = {"n1": _identity("n1", "KpiRow")}
        file_nodes = {
            "src/Chart.tsx": _file(
                "src/Chart.tsx",
                comp_names=["KpiRow", "Timeseries", "ChartHeader"],
            ),
        }
        plan = sa.analyze(decisions, identities, file_nodes)
        assert len(plan.splits) == 1

    def test_missing_file_node_no_split(self):
        sa = SPLITAnalyzer(threshold_component_count=3)
        decisions = {"n1": _decision("n1", Decision.UPDATE, "src/Chart.tsx")}
        identities = {"n1": _identity("n1", "KpiRow")}
        plan = sa.analyze(decisions, identities, {})
        assert len(plan.splits) == 0

    def test_dedup_multiple_decisions_same_component(self):
        sa = SPLITAnalyzer(threshold_component_count=3)
        decisions = {
            "n1": _decision("n1", Decision.UPDATE, "src/Chart.tsx"),
            "n2": _decision("n2", Decision.UPDATE, "src/Chart.tsx"),
        }
        identities = {
            "n1": _identity("n1", "KpiRow"),
            "n2": _identity("n2", "KpiRow"),
        }
        file_nodes = {
            "src/Chart.tsx": _file(
                "src/Chart.tsx",
                comp_names=["KpiRow", "Timeseries", "ChartHeader"],
            ),
        }
        plan = sa.analyze(decisions, identities, file_nodes)
        # Only one split for KpiRow despite two decisions
        assert len(plan.splits) == 1


class TestSPLITEmptyCases:
    """Empty and edge-case inputs."""

    def test_empty_decisions(self):
        sa = SPLITAnalyzer()
        id_n1 = _identity("n1", "KpiRow")
        plan = sa.analyze({}, {id_n1.graphir_node_id: id_n1}, {})
        assert plan.splits == []

    def test_empty_identities(self):
        sa = SPLITAnalyzer()
        plan = sa.analyze({"n1": _decision("n1")}, {}, {})
        assert plan.splits == []

    def test_empty_all(self):
        sa = SPLITAnalyzer()
        plan = sa.analyze({}, {}, {})
        assert plan.splits == []

    def test_crl_conflicts_not_yet_triggering_splits(self):
        """Phase 4.1: DUPLICATE_BINDING alone does not trigger split."""
        sa = SPLITAnalyzer(threshold_component_count=3)
        decisions = {"n1": _decision("n1", Decision.UPDATE, "src/KpiRow.tsx")}
        identities = {"n1": _identity("n1", "KpiRow")}
        file_nodes = {
            "src/KpiRow.tsx": _file("src/KpiRow.tsx", comp_names=["KpiRow"]),
        }
        conflicts = [
            ConflictRecord(
                identity_fingerprint="fp:one",
                conflict_type=ConflictType.DUPLICATE_BINDING,
                resolution=ResolutionStrategy.KEEP,
                expected_file="src/KpiRow.tsx",
                severity=0.5,
            ),
        ]
        plan = sa.analyze(decisions, identities, file_nodes, conflicts)
        # Single component file should not trigger
        assert len(plan.splits) == 0


class TestSPLITCustomThreshold:
    """Configurable threshold behavior."""

    def test_threshold_2_triggers(self):
        sa = SPLITAnalyzer(threshold_component_count=2)
        decisions = {"n1": _decision("n1", Decision.UPDATE, "src/Chart.tsx")}
        identities = {"n1": _identity("n1", "KpiRow")}
        file_nodes = {
            "src/Chart.tsx": _file(
                "src/Chart.tsx",
                comp_names=["KpiRow", "Timeseries"],
            ),
        }
        plan = sa.analyze(decisions, identities, file_nodes)
        assert len(plan.splits) == 1

    def test_threshold_1_never_creates(self):
        """Threshold=1 would split EVERY component — intentionally allowed
        but nonsensical. Test verifies it works correctly.
        """
        sa = SPLITAnalyzer(threshold_component_count=1)
        decisions = {"n1": _decision("n1", Decision.UPDATE, "src/Chart.tsx")}
        identities = {"n1": _identity("n1", "KpiRow")}
        file_nodes = {
            "src/Chart.tsx": _file(
                "src/Chart.tsx",
                comp_names=["KpiRow"],
            ),
        }
        plan = sa.analyze(decisions, identities, file_nodes)
        assert len(plan.splits) == 1
