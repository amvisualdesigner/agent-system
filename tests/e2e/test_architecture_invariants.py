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


def _context_for_workspace(workspace: str, artifacts: str):
    """RunContext scoped to an arbitrary seeded workspace."""
    from app.runtime.context import RunContext
    return RunContext(
        run_id=str(uuid.uuid4()),
        base_dir=workspace,
        workspace=workspace,
        artifacts=artifacts,
    )


def test_historical_memory_cannot_reinterpret_confirmed_create(create_workspace, run_context):
    """Run A: CREATE component -> materialized target. Run B: user confirms CREATE again.

    Architectural invariant (F1): historical memory must NOT reinterpret the
    confirmed CREATE into an UPDATE, and the engine must not silently
    overwrite an existing target. Run A must emit CREATE for KpiRow; Run B
    (target now present, no new-instance nomination) must yield an explicit
    conflict with zero modify operations.
    """
    ctx = _context_for_workspace(create_workspace, run_context.artifacts)

    # Build a simple CREATE plan for a KPI row (common contract mapping)
    plan = build_plan_from_actions([
        {"verb": "create", "target_capability": "presentation.kpi_row"}
    ])

    # Run A: apply the plan (dry_run=True to avoid persistent git commit,
    # but memory.save currently runs in the pipeline and will persist history).
    run_id_a = str(uuid.uuid4())
    res_a = apply_engine(run_id_a, plan.to_dict(), ctx, dry_run=True)

    # Expect a CREATE op in the emitted operations for KpiRow
    created = _ops_for_path(res_a, "KpiRow.tsx")
    assert any(op.get("action") == "create" for op in created), (
        f"Run A must emit a CREATE for KpiRow (seed run). Got: "
        f"{res_a.get('execution', {}).get('status')} "
        f"{res_a.get('execution', {}).get('detail')}"
    )

    # Ensure memory file was written (evidence persisted)
    mem_path = os.path.join(create_workspace, ".opencode", "semantic_memory.json")
    assert os.path.exists(mem_path), "semantic_memory.json should exist after run A"

    # Run B: new run confirming the same CREATE intent.
    # F1: CREATE on an already-materialized target must NOT be silently
    # reinterpreted as UPDATE (memory is evidence, never lifecycle) and must
    # NOT silently overwrite: it yields an explicit conflict (target exists,
    # no new-instance nomination) with zero modify operations.
    run_id_b = str(uuid.uuid4())
    res_b = apply_engine(run_id_b, plan.to_dict(), ctx, dry_run=True)

    status_b = res_b.get("execution", {}).get("status")
    detail_b = res_b.get("execution", {}).get("detail", "")
    assert status_b == "clarification_needed", (
        f"Confirmed CREATE on existing target must yield explicit conflict, "
        f"got status={status_b!r} detail={detail_b!r} (FAIL)."
    )
    ops_b = _ops_for_path(res_b, "KpiRow.tsx")
    actions = [op.get("action") for op in ops_b]
    assert "modify" not in actions, (
        "Confirmed CREATE was reinterpreted as UPDATE — memory has influenced "
        "lifecycle (FAIL)."
    )
    assert "presentation.kpi_row" in detail_b, (
        f"Conflict must cite the existing target capability, got detail={detail_b!r}."
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
    """F5: SPLITAnalyzer is analysis/evidence ONLY — it must never produce a FileOp.

    Invariants demonstrated:
      1. SPLITAnalyzer CAN detect/propose a split (structural overload).
      2. That proposal alone produces NO FileOp.
      3. The renderer does NOT read or materialize split_plan (it is not even
         part of pipeline state anymore).
      4. The confirmed plan target is unchanged: the KpiRow MODIFY still
         targets src/components/BigFile.tsx, never the split new_file.
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

    # Decisions: pretend the dashboard.kpi node maps to BigFile (confirmed plan target)
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
    # Build a minimal RenderContext-like structure expected by renderer.render.
    # F5: pipeline state carries NO split_plan — the renderer never received it.
    from app.graphir.constraint.context import PipelineState, RenderContext
    ps = PipelineState(file_nodes=file_nodes, component_nodes={}, decisions=decisions, resolved_mapping={})
    ctx = RenderContext(execution=ps, feature_flags={})

    fileops = renderer.render(graph, layout, config=type("C", (), {"component_signatures": {}, "output_base_path": "src/components", "path_map": {}, "file_extension": ".tsx", "file_path_overrides": {}})(), context=ctx, existing_content_by_path={})

    # 2+3. The SPLIT proposal produces NO FileOp on its own
    emitted_paths = [op.path for op in fileops]
    for nf in new_files:
        assert nf not in emitted_paths, (
            f"Split plan was materialized automatically (found {nf} in renderer output)."
        )

    # 4. The confirmed plan target is unchanged — KpiRow MODIFY targets BigFile
    assert any(
        op.path == "src/components/BigFile.tsx" for op in fileops
    ), f"Confirmed plan target must still be emitted: got {[(op.action, op.path) for op in fileops]}"


def test_create_over_existing_component_in_custom_named_file_yields_conflict(create_workspace, run_context):
    """F4/R-a: a confirmed CREATE must never overwrite an existing physical
    target, even when the target file uses a name the structural index cannot
    see (custom-named file). The content-based index DOES see the component, so
    the resolved target_file exists -> explicit conflict, file untouched.
    """
    ctx = _context_for_workspace(create_workspace, run_context.artifacts)

    # KpiRow exists ONLY inside a custom-named file (invisible to the
    # filename-exact structural index, visible to the content index).
    overview = "src/components/Overview.tsx"
    full = os.path.join(create_workspace, overview)
    with open(full, "w") as f:
        f.write("export const KpiRow = () => null;")
    with open(full) as f:
        original = f.read()

    plan = build_plan_from_actions([
        {"verb": "create", "target_capability": "presentation.kpi_row"}
    ])

    res = apply_engine(str(uuid.uuid4()), plan.to_dict(), ctx, dry_run=True)

    status = res.get("execution", {}).get("status")
    detail = res.get("execution", {}).get("detail", "")
    assert status == "clarification_needed", (
        f"CREATE over existing physical target must conflict, got status={status!r} detail={detail!r}"
    )
    assert res.get("execution", {}).get("conflict") == "repository_conflict", detail

    # The existing file is untouched and no phantom KpiRow.tsx was spawned
    with open(full) as f:
        assert f.read() == original, "CREATE must not overwrite the existing physical target"
    assert not os.path.exists(os.path.join(create_workspace, "src/components/KpiRow.tsx")), (
        "No implicit alternative target must be materialized"
    )


def test_modify_over_multiple_instances_without_selection_yields_conflict(run_context):
    """F4/R-b: a MODIFY whose capability maps to N>1 physical instances and the
    plan does not nominate a unique target must yield an explicit conflict — the
    engine must never silently pick the first file.
    """
    ws = run_context.workspace
    os.makedirs(os.path.join(ws, "src/components/a"), exist_ok=True)
    os.makedirs(os.path.join(ws, "src/components/b"), exist_ok=True)
    with open(os.path.join(ws, "src/components/a/KpiRow.tsx"), "w") as f:
        f.write("export const KpiRow = () => null;")
    with open(os.path.join(ws, "src/components/b/KpiRow.tsx"), "w") as f:
        f.write("export const KpiRow = () => null;")

    plan = build_plan_from_actions([
        {"verb": "modify", "target_capability": "presentation.kpi_row"}
    ])

    res = apply_engine(str(uuid.uuid4()), plan.to_dict(), run_context, dry_run=True)

    status = res.get("execution", {}).get("status")
    assert status == "clarification_needed", (
        f"Ambiguous MODIFY (N instances, no selection) must yield a conflict, got: {status}"
    )
    res_ops = res.get("execution", {}).get("operations", [])
    assert not any(op.get("action") == "modify" for op in res_ops), (
        f"No silent MODIFY may be emitted: got {res_ops}"
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
