"""F4 lock tests: RepositoryValidation VALID/CONFLICT matrix.

The Confirmed Plan (StructuralIR.operations) is the sole lifecycle authority.
Physical repository existence NEVER reinterprets lifecycle; it only gates
materialization. These tests pin the exact VALID/CONFLICT behavior of
``_validate_repository_matrix``:

  R1.  CREATE + target already in repo + no instance_hint          -> CONFLICT
  R1b. CREATE + target already in repo + instance_hint + free path -> VALID
  R1c. CREATE + target already in repo + instance_hint + collision -> CONFLICT
  R2.  MODIFY + capability not present in repo                     -> CONFLICT
  R3.  DELETE + capability not present in repo                     -> CONFLICT
  R7.  RepositoryValidation never mutates the plan (pure observer).
"""
from __future__ import annotations

import pytest

from app.engine.apply_engine import (
    _validate_create_physical_targets,
    _validate_repository_matrix,
)
from app.engine.state_adapter import ComponentInstanceInfo
from app.engine.structural_completion import (
    CREATE,
    DELETE,
    KEEP,
    MODIFY,
    CompletionMode,
    ResolvedCapability,
    StructuralIR,
)
from app.engine.structural_index import StructuralIndex
from app.graphir.constraint.models import Decision, FileNode, FileOpDecision
from app.graphir.structure.models import StructuralResolution


# ─── Builders ───


def _instance(
    capability: str,
    path: str,
    file_path: str,
    instance_id: str = "0",
) -> ComponentInstanceInfo:
    return ComponentInstanceInfo(
        capability=capability,
        path=path,
        file_path=file_path,
        instance_id=instance_id,
    )


def _resolution(payload: dict[str, str]) -> StructuralResolution:
    return StructuralResolution(
        capability_to_path=payload,
        confidence=1.0,
        trace=[],
        instance_mapping={},
        reason=None,
    )


def _ir(*caps: ResolvedCapability) -> StructuralIR:
    return StructuralIR(
        contract_id="dashboard.sales_overview",
        contract_version=1,
        capabilities=tuple(caps),
        param_provenance={},
        confidence=1.0,
    )


def _cap(
    name: str,
    action: str,
    instance_hint: str | None = None,
    instance_only: bool = False,
) -> ResolvedCapability:
    return ResolvedCapability(
        name=name,
        params={},
        mode=CompletionMode.SAFE_COMPLETE,
        action=action,
        instance_only=instance_only,
        instance_hint=instance_hint,
    )


KPI = "presentation.kpi_row"

# Single-instance index as the state_adapter would build it
_KPI_INDEX = StructuralIndex.from_mapping({
    KPI: [_instance(KPI, "kpi_row", "src/components/KpiRow.tsx", "0")],
})


def _conflict(result: dict | None) -> dict:
    assert result is not None, "expected a conflict, got None (VALID)"
    assert result["execution"]["status"] == "clarification_needed", result
    return result


# ─── R1: CREATE + existing target + no instance_hint -> CONFLICT ───


def test_r1_create_on_existing_target_without_hint_is_conflict():
    ir = _ir(_cap(KPI, CREATE))
    res = _conflict(_validate_repository_matrix(ir, _KPI_INDEX, _resolution({})))
    assert res["execution"]["conflict"] == "repository_conflict"
    assert "instance_hint" in res["execution"]["detail"]
    assert res["execution"]["operations"] == []


# ─── R1b: CREATE + instance_hint + free projected path -> VALID ───


def test_r1b_create_with_hint_and_free_path_is_valid():
    ir = _ir(_cap(KPI, CREATE, instance_hint="NewKpi"))
    res = _validate_repository_matrix(
        ir, _KPI_INDEX, _resolution({KPI: "src/components/NewKpi.tsx"}),
    )
    assert res is None, f"expected VALID (new instance, no collision), got {res}"


# ─── R1c: CREATE + instance_hint + projected path collides -> CONFLICT ───


def test_r1c_create_with_hint_but_colliding_path_is_conflict():
    ir = _ir(_cap(KPI, CREATE, instance_hint="KpiRow"))
    res = _conflict(
        _validate_repository_matrix(
            ir, _KPI_INDEX, _resolution({KPI: "src/components/KpiRow.tsx"}),
        )
    )
    assert res["execution"]["conflict"] == "repository_conflict"
    assert "collide" in res["execution"]["detail"]


# ─── R2: MODIFY + capability not present -> CONFLICT ───


def test_r2_modify_missing_target_is_conflict():
    ir = _ir(_cap("presentation.timeseries", MODIFY))
    res = _conflict(_validate_repository_matrix(ir, _KPI_INDEX, _resolution({})))
    assert res["execution"]["conflict"] == "repository_conflict"
    assert "MODIFY" in res["execution"]["detail"]
    assert res["execution"]["operations"] == []


# ─── R3: DELETE + capability not present -> CONFLICT ───


def test_r3_delete_missing_target_is_conflict():
    ir = _ir(_cap("presentation.timeseries", DELETE))
    res = _conflict(_validate_repository_matrix(ir, _KPI_INDEX, _resolution({})))
    assert res["execution"]["conflict"] == "repository_conflict"
    assert "DELETE" in res["execution"]["detail"]


# ─── MODIFY single instance -> VALID (R-b: N == 1) ───


def test_modify_single_instance_is_valid():
    ir = _ir(_cap(KPI, MODIFY))
    res = _validate_repository_matrix(ir, _KPI_INDEX, _resolution({}))
    assert res is None, f"expected VALID for single-instance MODIFY, got {res}"


# ─── No matrix dependency on resolution for non-CREATE ops ───


def test_matrix_does_not_require_resolution_for_modify_delete():
    ir = _ir(_cap(KPI, MODIFY), _cap("presentation.timeseries", DELETE))
    res = _validate_repository_matrix(ir, _KPI_INDEX, None)
    assert res is not None
    assert "DELETE" in res["execution"]["detail"] or "MODIFY" in res["execution"]["detail"]


# ─── R7: matrix never mutates the plan ───


def test_r7_matrix_does_not_mutate_the_confirmed_plan():
    cap_create = _cap(KPI, CREATE)
    cap_missing = _cap("presentation.timeseries", MODIFY)
    ir = _ir(cap_create, cap_missing)
    before = ir.capabilities

    res = _conflict(_validate_repository_matrix(ir, _KPI_INDEX, _resolution({})))

    assert ir.capabilities == before
    assert [rc.action for rc in ir.capabilities] == [CREATE, MODIFY]
    assert ir.capabilities[0].name == KPI
    assert ir.capabilities[1].name == "presentation.timeseries"
    assert res["execution"]["conflict"] == "repository_conflict"


# ─── R-b: MODIFY + N instances without explicit selection -> CONFLICT ───

_MULTI_KPI_INDEX = StructuralIndex.from_mapping({
    KPI: [
        _instance(KPI, "kpi_row", "src/components/a/KpiRow.tsx", "0"),
        _instance(KPI, "kpi_row:1", "src/components/b/KpiRow.tsx", "1"),
    ],
})


def test_rb_modify_n_instances_without_selection_is_ambiguity():
    ir = _ir(_cap(KPI, MODIFY))
    res = _conflict(_validate_repository_matrix(ir, _MULTI_KPI_INDEX, _resolution({})))
    assert res["execution"]["conflict"] == "ambiguity"
    assert "instances" in res["execution"]["detail"].lower()
    assert "silent choice" in res["execution"]["detail"].lower()
    assert "src/components/a/KpiRow.tsx" in res["execution"]["detail"]
    assert "src/components/b/KpiRow.tsx" in res["execution"]["detail"]


def test_rb_modify_n_instances_still_valid_for_cardinality_one():
    res = _validate_repository_matrix(_ir(_cap(KPI, MODIFY)), _KPI_INDEX, None)
    assert res is None


def test_rb_delete_multi_instance_remains_matrix_valid():
    """R-b is scoped to MODIFY. Multi-instance DELETE is validated later in the
    pipeline (agent confirmation); the matrix must not preempt it."""
    res = _validate_repository_matrix(_ir(_cap(KPI, DELETE)), _MULTI_KPI_INDEX, _resolution({}))
    assert res is None, f"multi-instance DELETE belongs to the delete gate, got {res}"


# ─── R-a: CREATE must not materialize over an existing physical target ───


def _file_node(path: str) -> FileNode:
    return FileNode(id=path, node_type="file", path=path, component_names=[], exports=[])


def _decision(intent_id: str, node_id: str, decision: Decision, target_file: str) -> FileOpDecision:
    return FileOpDecision(
        intent_id=intent_id,
        graphir_node_id=node_id,
        decision=decision,
        target_file=target_file,
        render_mode="create",
    )


def test_ra_create_resolved_to_existing_file_is_conflict():
    file_nodes = {
        "src/components/Overview.tsx": _file_node("src/components/Overview.tsx"),
    }
    decisions = {
        "kpi": _decision(KPI, "kpi", Decision.CREATE, "src/components/Overview.tsx"),
    }
    plan_actions = {KPI: CREATE}
    res = _conflict(_validate_create_physical_targets(decisions, plan_actions, file_nodes))
    assert res["execution"]["conflict"] == "repository_conflict"
    assert "Overview.tsx" in res["execution"]["detail"]
    assert res["execution"]["operations"] == []


def test_ra_create_resolved_to_new_file_passes():
    file_nodes = {
        "src/components/Overview.tsx": _file_node("src/components/Overview.tsx"),
    }
    decisions = {
        "kpi": _decision(KPI, "kpi", Decision.CREATE, "src/components/KpiRow.tsx"),
    }
    res = _validate_create_physical_targets(decisions, {KPI: CREATE}, file_nodes)
    assert res is None, f"CREATE to a free path must pass, got {res}"


def test_ra_guard_only_gates_create_plan_actions():
    """A MODIFY whose decision targets an existing file is legal — the guard is
    scoped to confirmed CREATE actions only."""
    file_nodes = {
        "src/components/KpiRow.tsx": _file_node("src/components/KpiRow.tsx"),
    }
    decisions = {
        "kpi": _decision(KPI, "kpi", Decision.UPDATE, "src/components/KpiRow.tsx"),
    }
    res = _validate_create_physical_targets(decisions, {KPI: MODIFY}, file_nodes)
    assert res is None, f"MODIFY targeting an existing file must pass, got {res}"


def test_ra_guard_skips_unattributable_decisions():
    """Only decisions tied to a confirmed plan CREATE are gated."""
    file_nodes = {
        "src/components/KpiRow.tsx": _file_node("src/components/KpiRow.tsx"),
    }
    decisions = {
        "kpi": _decision(KPI, "kpi", Decision.CREATE, "src/components/KpiRow.tsx"),
    }
    res = _validate_create_physical_targets(decisions, {}, file_nodes)
    assert res is None, f"unattributable decision (no plan action) must pass, got {res}"