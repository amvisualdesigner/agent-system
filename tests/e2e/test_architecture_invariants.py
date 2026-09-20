"""Architectural invariant tests (TESTS FIRST).

These tests assert desired architectural boundaries. They must NOT modify
production code. Some of these tests are expected to fail with the current
implementation; failures are intentional and surface architectural violations.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.engine.apply_engine import apply_engine
from app.graphir.constraint.crl import ConflictResolutionLayer
from app.graphir.constraint.models import FileNode, ConflictType
from app.graphir.constraint.split_analyzer import SPLITAnalyzer
from app.graphir.constraint.renderer import RepositoryAwareRenderer
from app.graphir.constraint.models import FileOpDecision, Decision, MemoryRecord
from tests.e2e.helpers import build_plan_from_actions
from tests.helpers import make_sample_graph


def _ops_for_path(result: dict, path_suffix: str) -> list[dict]:
    ops = result.get("execution", {}).get("operations", [])
    return [op for op in ops if path_suffix in op.get("path", "")]


def test_historical_memory_cannot_reinterpret_confirmed_create(create_workspace, run_context):
    """Run A: CREATE component -> persisted memory. Run B: user confirms CREATE again.

    Architectural invariant: historical memory must NOT reinterpret the confirmed
    CREATE into an UPDATE. The test asserts the confirmed CREATE remains CREATE.
    (If the current system reinterprets it as UPDATE, this test will FAIL.)
    """
    # Build a simple CREATE plan for a KPI row (common contract mapping)
    plan = build_plan_from_actions([
        {"verb": "create", "target_capability": "presentation.kpi_row"}
    ])

    # Run A: apply the plan (dry_run=True to avoid persistent git commit,
    # but memory.save currently runs in the pipeline and will persist history).
    run_id_a = str(uuid.uuid4())
    res_a = apply_engine(run_id_a, plan.to_dict(), run_context, dry_run=True)

    # Expect a CREATE op in the emitted operations for KpiRow
    created = _ops_for_path(res_a, "KpiRow.tsx")
    assert any(op.get("action") == "create" for op in created), (
        "Run A must emit a CREATE for KpiRow (seed run)."
    )

    # Ensure memory file was written (evidence persisted)
    mem_path = os.path.join(run_context.workspace, ".opencode", "semantic_memory.json")
    assert os.path.exists(mem_path), "semantic_memory.json should exist after run A"

    # Run B: new run confirming the same CREATE intent
    run_id_b = str(uuid.uuid4())
    res_b = apply_engine(run_id_b, plan.to_dict(), run_context, dry_run=True)

    # Architectural invariant: the confirmed CREATE must remain CREATE.
    ops_b = _ops_for_path(res_b, "KpiRow.tsx")
    actions = [op.get("action") for op in ops_b]
    assert "create" in actions, (
        "Confirmed CREATE was reinterpreted — memory has influenced lifecycle (FAIL)."
    )


def test_apply_engine_detects_plan_mismatch_and_returns_conflict(run_context):
    """If a confirmed plan cannot be uniquely applied (ambiguous targets),
    the engine must return a clarification/conflict result rather than
    silently reinterpreting or applying a different plan.

    This test creates ambiguous on-disk state for a delete target and expects
    `apply_engine` to return a `clarification_needed` style response.
    """
    # Create two files that both map to the same capability name (KpiRow)
    ws = run_context.workspace
    os.makedirs(os.path.join(ws, "src/components/a"), exist_ok=True)
    os.makedirs(os.path.join(ws, "src/components/b"), exist_ok=True)
    with open(os.path.join(ws, "src/components/a/KpiRow.tsx"), "w") as f:
        f.write("export const KpiRow = () => null;")
    with open(os.path.join(ws, "src/components/b/KpiRow.tsx"), "w") as f:
        f.write("export const KpiRow = () => null;")

    # Commit-like setup (git not required for this test path)

    # Build a delete plan for the KPI capability
    plan = build_plan_from_actions([
        {"verb": "remove", "target_capability": "presentation.kpi_row"}
    ])

    res = apply_engine(str(uuid.uuid4()), plan.to_dict(), run_context, dry_run=True)

    status = res.get("execution", {}).get("status")
    assert status == "clarification_needed", (
        f"Expected clarification_needed for ambiguous delete, got: {status}"
    )


def test_split_plan_is_suggestion_not_automatic_refactor():
    """SPLITAnalyzer should be a suggestion; renderer must NOT automatically
    materialize refactors for a confirmed plan without explicit confirmation.

    This test asserts that the renderer does NOT emit CREATE for split targets.
    Under current implementation the renderer honors `split_plan` and will
    generate CREATE ops (so this test is expected to FAIL until cleanup).
    """
    graph, layout = make_sample_graph(kind="dashboard")

    # Prepare a fake file_nodes dict where a single file contains many components
    # Include the exact component name the identity will report (KpiRow)
    file_nodes = {
        "src/components/BigFile.tsx": FileNode(
            id="f1",
            node_type="file",
            path="src/components/BigFile.tsx",
            component_names=["A", "B", "C", "KpiRow"],
            exports=["A", "B", "C", "KpiRow"],
        )
    }

    # Decisions: pretend the dashboard.kpi node maps to BigFile
    decisions = {
        "kpi": FileOpDecision(
            intent_id="presentation.kpi_row",
            graphir_node_id="kpi",
            decision=Decision.UPDATE,
            target_file="src/components/BigFile.tsx",
            confidence=0.9,
            rationale="test",
            render_mode="modify",
        )
    }

    identities = {"kpi": type("CI", (), {"component_name": "KpiRow", "fingerprint": lambda self=None: "fp_kpi"})()}

    # Run analyzer -> renderer
    analyzer = SPLITAnalyzer(threshold_component_count=3)
    split_plan = analyzer.analyze(decisions, identities, file_nodes, crl_conflicts=None)

    # Ensure the analyzer actually proposed at least one split for this scenario
    assert getattr(split_plan, "splits", None), "SPLITAnalyzer did not propose any splits — test setup invalid"
    new_files = [s.new_file for s in split_plan.splits]
    assert new_files and all(isinstance(nf, str) and nf for nf in new_files), "Split directives must include concrete new_file paths"

    renderer = RepositoryAwareRenderer()
    # Build a minimal RenderContext-like structure expected by renderer.render
    from app.graphir.constraint.context import PipelineState, RenderContext
    ps = PipelineState(file_nodes=file_nodes, component_nodes={}, decisions=decisions, split_plan=split_plan, resolved_mapping={})
    ctx = RenderContext(execution=ps, feature_flags={})

    fileops = renderer.render(graph, layout, config=type("C", (), {"component_signatures": {}, "output_base_path": "src/components", "path_map": {}, "file_extension": ".tsx", "file_path_overrides": {}})(), context=ctx, existing_content_by_path={})

    # Assert that renderer does NOT emit CREATE for the split new_file (desired invariant)
    # The SPLITAnalyzer will propose a new file; detect whether renderer created it.
    emitted_paths = [op.path for op in fileops]
    for nf in new_files:
        assert nf not in emitted_paths, (
            f"Split plan was materialized automatically (found {nf} in renderer output)."
        )


def test_crl_detects_stale_missing_duplicate_and_never_rebinds():
    # Prepare memory mapping with stale, missing-target and duplicate bindings
    memory_mapping = {
        "fp_stale": "no/such/File.tsx",
        "fp_empty": "src/empty.tsx",
        "fp_dup1": "src/dup.tsx",
        "fp_dup2": "src/dup.tsx",
    }

    # file_nodes: only src/empty.tsx exists but has no exports/components
    file_nodes = {
        "src/empty.tsx": FileNode(id="f_empty", node_type="file", path="src/empty.tsx", component_names=[], exports=[]),
        # Note: dup path present
        "src/dup.tsx": FileNode(id="f_dup", node_type="file", path="src/dup.tsx", component_names=["X"], exports=["X"]),
    }

    crl = ConflictResolutionLayer()
    cleaned, conflicts = crl.resolve(memory_mapping, file_nodes)

    # stale entry removed
    assert "fp_stale" not in cleaned
    # missing target (empty file) removed
    assert "fp_empty" not in cleaned
    # duplicates: both fingerprints may be kept but reported as conflicts
    dup_conflicts = [c for c in conflicts if c.conflict_type == ConflictType.DUPLICATE_BINDING]
    assert dup_conflicts, "Expected DUPLICATE_BINDING conflict for dup.tsx"

    # Ensure CRL did not rebind fingerprints to new arbitrary paths (no REBIND)
    for c in conflicts:
        assert c.resolution != "rebind"
