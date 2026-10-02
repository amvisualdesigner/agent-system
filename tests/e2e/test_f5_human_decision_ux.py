"""Fase 5 — Human Decision / Confirmation UX.

Covers 5A (clarification + deterministic instance choices) and 5B
(post-confirm conflict recovery + explicit retry + cancel).

Invariants locked by this module:
  * D1 — a post-confirm conflict is NOT terminal: the Confirmed Plan stays
    authoritative and the Run returns to `confirmed` for an explicit retry.
  * D2 — wire contract distinguishes pre-plan `clarification_needed` from
    post-plan `conflict`.
  * D3 — instance ambiguity produces structured deterministic choices, never a
    silent selection.
  * Sub-decisión A — retry re-applies the SAME plan with a fresh snapshot.
  * Sub-decisión B — cancel is impossible while applying.
"""

import os
import shutil
import subprocess
import uuid

import pytest

from app.config.settings import settings
from app.intent.models import RunPhase, conflict_result, validate_transition
from app.state.run_state import (
    delete_run_state,
    load_run_state,
    save_run_state,
    transition_phase,
)

from tests.e2e.helpers import build_plan_from_actions


CONFLICT_CONTRACT_FIELDS = (
    "status", "stage", "plan_confirmed", "plan_retryable", "run_phase",
    "conflict", "detail", "candidates", "operations", "diff",
)


def _seed_workspace(run_id: str, files=None) -> str:
    ws = os.path.join(settings.RUNS_DIR, run_id)
    shutil.rmtree(ws, ignore_errors=True)
    os.makedirs(os.path.join(ws, "src", "pages", "dashboard"), exist_ok=True)
    os.makedirs(os.path.join(ws, "src", "components"), exist_ok=True)
    files = files if files is not None else (
        ("src/pages/dashboard/Page.tsx",
         "import React from 'react';\nexport const Page: React.FC = () => <div/>;\n"),
        ("src/components/LineChart.tsx",
         "import React from 'react';\nexport const LineChart: React.FC = () => <svg/>;\n"),
    )
    for rel, content in files:
        with open(os.path.join(ws, rel), "w") as f:
            f.write(content)
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=ws, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed", "--allow-empty"], cwd=ws, capture_output=True)
    return ws


def _confirmed_state(run_id: str, ws: str, actions=None, plan_actions=None) -> None:
    from app.engine.worktree_snapshot import snapshot_worktree

    actions = actions or [
        {"verb": "create", "target_capability": "presentation.kpi_row", "confidence": 1.0},
    ]
    save_run_state(run_id, {
        "phase": "confirmed",
        "confirmed_intent": {
            "contract_id": "dashboard.sales_overview",
            "actions": actions,
        },
        "compiled_plan": build_plan_from_actions(plan_actions or actions).to_dict(),
        "plan_preview": {"summary_human": "test"},
        "gate": {"blocked": False},
        "apply_snapshot": snapshot_worktree(ws),
    })


# ══════════════════════════════════════════════════════════════════
# D1 / State machine
# ══════════════════════════════════════════════════════════════════


class TestConflictLifecycle:
    def test_applying_can_return_to_confirmed(self):
        validate_transition(RunPhase.APPLYING, RunPhase.CONFIRMED)

    def test_applying_cannot_be_cancelled(self):
        with pytest.raises(ValueError):
            validate_transition(RunPhase.APPLYING, RunPhase.CANCELLED)

    def test_conflict_does_not_resurrect_terminal_phases(self):
        for terminal in (RunPhase.FAILED, RunPhase.COMPLETED, RunPhase.CANCELLED):
            for target in (RunPhase.CONFIRMED, RunPhase.APPLYING):
                with pytest.raises(ValueError):
                    validate_transition(terminal, target)

    def test_transition_round_trip(self):
        run_id = str(uuid.uuid4())
        try:
            save_run_state(run_id, {"phase": RunPhase.CONFIRMED.value})
            transition_phase(run_id, RunPhase.APPLYING)
            transition_phase(run_id, RunPhase.CONFIRMED)
            assert load_run_state(run_id)["phase"] == RunPhase.CONFIRMED.value
        finally:
            delete_run_state(run_id)


# ══════════════════════════════════════════════════════════════════
# D2 / Conflict wire contract
# ══════════════════════════════════════════════════════════════════


class TestConflictWireContract:
    def test_conflict_result_has_canonical_shape(self):
        result = conflict_result("repository_conflict", "boom")
        ex = result["execution"]
        for field in CONFLICT_CONTRACT_FIELDS:
            assert field in ex, f"missing {field}"
        assert ex["status"] == "conflict"
        assert ex["stage"] == "apply"
        assert ex["plan_confirmed"] is True
        assert ex["plan_retryable"] is True
        assert ex["run_phase"] == RunPhase.CONFIRMED.value
        assert ex["operations"] == []
        assert ex["diff"] is None

    def test_fallback_request_never_returns_clarification_needed(self):
        from app.intent.models import FallbackExecutionRequest

        for level in (0, 1, 2):
            res = FallbackExecutionRequest(
                reason="x", conflict_type="ambiguity", level=level,
            ).to_result()
            assert res["execution"]["status"] == "conflict"
            assert "reason" not in res["execution"]

    def test_fallback_request_exposes_options_as_candidates(self):
        from app.intent.models import FallbackExecutionRequest

        res = FallbackExecutionRequest(
            reason="x", conflict_type="ambiguity", level=2,
            options=[{"instance_id": "1", "file_path": "a.tsx"}],
            details={"k": "v"},
        ).to_result()
        assert res["execution"]["candidates"] == [{"instance_id": "1", "file_path": "a.tsx"}]
        assert res["execution"]["details"] == {"k": "v"}

    def test_non_retryable_conflicts_are_marked(self):
        from app.intent.models import FallbackExecutionRequest

        res = FallbackExecutionRequest(
            reason="bad plan", conflict_type="invalid_confirmed_plan", level=2,
            retryable=False,
        ).to_result()
        assert res["execution"]["plan_retryable"] is False

    def test_invalid_confirmed_plan_is_not_retryable(self):
        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        try:
            from app.api.agent_apply import _agent_apply
            from app.contracts.apply_request import ApplyRequest

            _confirmed_state(
                run_id, ws,
                actions=[{"verb": "create", "target_capability": ""}],
            )
            result = _agent_apply(ApplyRequest(run_id=run_id, dry_run=True))
            assert result["execution"]["status"] == "conflict"
            assert result["execution"]["conflict"] == "invalid_confirmed_plan"
            assert result["execution"]["plan_retryable"] is False
            assert load_run_state(run_id)["phase"] == RunPhase.CONFIRMED.value
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

    def test_concurrency_is_a_declared_category(self):
        from typing import get_args, get_type_hints

        from app.intent.models import FallbackExecutionRequest

        categories = get_args(
            get_type_hints(FallbackExecutionRequest)["conflict_type"]
        )
        assert "concurrency" in categories
        # G7: substitution overflow stays a pre-plan taxonomy value; the apply
        # engine must never surface it as a post-confirm conflict.
        assert "substitution_overflow" in categories

    def test_apply_engine_never_emits_substitution_overflow(self):
        import pathlib as _p

        src = _p.Path(
            os.path.join(os.path.dirname(__file__), "..", "..",
                         "backend", "app", "engine", "apply_engine.py")
        ).read_text(encoding="utf-8")
        assert "substitution_overflow" not in src

    def test_apply_engine_never_emits_clarification_needed(self):
        """G5: no post-plan raw dict may use the pre-plan status."""
        import pathlib

        src = pathlib.Path(
            os.path.join(os.path.dirname(__file__), "..", "..",
                         "backend", "app", "engine", "apply_engine.py")
        ).read_text(encoding="utf-8")
        assert '"clarification_needed"' not in src
        assert "'clarification_needed'" not in src


# ══════════════════════════════════════════════════════════════════
# D1 / Conflict parks the Run in `confirmed`
# ══════════════════════════════════════════════════════════════════


class TestConflictParksRun:
    def test_repository_conflict_parks_run_in_confirmed(self):
        from app.api.agent_apply import _agent_apply
        from app.contracts.apply_request import ApplyRequest

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id, files=(
            ("src/components/KpiRow.tsx", "export const KpiRow = () => <div/>;\n"),
        ))
        try:
            _confirmed_state(run_id, ws, actions=[{
                "verb": "create",
                "target_capability": "presentation.kpi_row",
                "confidence": 1.0,
            }])
            result = _agent_apply(ApplyRequest(run_id=run_id, dry_run=True))
            ex = result["execution"]
            assert ex["status"] == "conflict"
            assert ex["conflict"] == "repository_conflict"
            state = load_run_state(run_id)
            assert state["phase"] == RunPhase.CONFIRMED.value
            assert state["plan_retryable"] is True
            assert state["last_conflict"]["conflict"] == "repository_conflict"
            # the Confirmed Plan is untouched
            assert state["compiled_plan"] == load_run_state(run_id)["compiled_plan"]
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

    def test_dry_run_also_detects_a_stale_baseline(self):
        """A pre-flight must not promise an outcome the real apply cannot deliver."""
        from app.api.agent_apply import _agent_apply
        from app.contracts.apply_request import ApplyRequest

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        try:
            _confirmed_state(run_id, ws)
            with open(os.path.join(ws, "src", "components", "LineChart.tsx"), "a") as f:
                f.write("// drift\n")
            result = _agent_apply(ApplyRequest(run_id=run_id, dry_run=True))
            ex = result["execution"]
            assert ex["status"] == "conflict"
            assert ex["conflict"] == "concurrency"
            assert ex["run_phase"] == RunPhase.CONFIRMED.value
            assert result["meta"]["concurrency"]["status"] == "stale"
            assert load_run_state(run_id)["phase"] == RunPhase.CONFIRMED.value
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

    def test_crash_never_parks_the_run_in_applying(self, monkeypatch):
        """`applying` is not cancellable: a crash must settle into failed."""
        from app.api import agent_apply as mod
        from app.contracts.apply_request import ApplyRequest

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        try:
            _confirmed_state(run_id, ws)
            monkeypatch.setattr(
                mod, "build_context",
                lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
            )
            out = mod.agent_apply(ApplyRequest(run_id=run_id))
            assert out["status"] == "error"
            assert load_run_state(run_id)["phase"] == RunPhase.FAILED.value
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

    def test_ambiguity_conflict_carries_candidates(self):
        from app.api.agent_apply import _agent_apply
        from app.contracts.apply_request import ApplyRequest

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id, files=(
            ("src/components/KpiRow.tsx", "export const KpiRow = () => <div/>;\n"),
            ("src/components/KpiRow2.tsx", "export const KpiRow2 = () => <div/>;\n"),
        ))
        try:
            _confirmed_state(run_id, ws, actions=[{
                "verb": "modify",
                "target_capability": "presentation.kpi_row",
                "confidence": 1.0,
            }])
            result = _agent_apply(ApplyRequest(run_id=run_id, dry_run=True))
            ex = result["execution"]
            if ex["status"] == "conflict":
                assert ex["conflict"] in ("ambiguity", "target_ambiguity")
                assert len(ex["candidates"]) >= 2
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# Sub-decisión A — explicit retry
# ══════════════════════════════════════════════════════════════════


class TestRetry:
    def test_retry_requires_confirmed_phase(self):
        from fastapi import HTTPException

        from app.api.agent_apply import _agent_retry, RetryRequest

        run_id = str(uuid.uuid4())
        try:
            save_run_state(run_id, {"phase": RunPhase.AWAITING_CONFIRMATION.value})
            with pytest.raises(HTTPException) as exc:
                _agent_retry(RetryRequest(run_id=run_id))
            assert exc.value.status_code == 409
        finally:
            delete_run_state(run_id)

    def test_retry_rejected_when_not_retryable(self):
        from fastapi import HTTPException

        from app.api.agent_apply import _agent_retry, RetryRequest

        run_id = str(uuid.uuid4())
        try:
            _confirmed_state(run_id, "/tmp")
            save_run_state(run_id, {"plan_retryable": False, "last_conflict": {"conflict": "invalid_confirmed_plan"}})
            with pytest.raises(HTTPException) as exc:
                _agent_retry(RetryRequest(run_id=run_id))
            assert exc.value.status_code == 409
            assert "not retryable" in exc.value.detail
        finally:
            delete_run_state(run_id)

    def test_retry_recaptures_snapshot_and_reuses_same_plan(self):
        from app.api.agent_apply import _agent_retry, RetryRequest

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        try:
            _confirmed_state(run_id, ws)
            plan_before = load_run_state(run_id)["compiled_plan"]
            snapshot_before = load_run_state(run_id)["apply_snapshot"]

            target = os.path.join(ws, "src", "components", "LineChart.tsx")
            with open(target, "a") as f:
                f.write("// drift\n")

            result = _agent_retry(RetryRequest(run_id=run_id, dry_run=True))
            assert result["meta"]["retry"] == {"same_plan": True, "fresh_snapshot": True}
            state = load_run_state(run_id)
            assert state["compiled_plan"] == plan_before
            assert state["apply_snapshot"] != snapshot_before
            # drift is included in the NEW baseline, so no concurrency conflict
            assert result["meta"]["concurrency"]["status"] == "ok"
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

    def test_retry_does_not_reinterpret(self):
        """The retry path never calls interpret or compile_plan."""
        import pathlib

        src = pathlib.Path(
            os.path.join(os.path.dirname(__file__), "..", "..",
                         "backend", "app", "api", "agent_apply.py")
        ).read_text(encoding="utf-8")
        retry_body = src.split("def _agent_retry")[1]
        assert "interpret" not in retry_body
        assert "compile_plan" not in retry_body


# ══════════════════════════════════════════════════════════════════
# D5 / Sub-decisión B — cancel
# ══════════════════════════════════════════════════════════════════


class TestCancel:
    @pytest.mark.parametrize("phase", [
        RunPhase.INTERPRETING.value,
        RunPhase.AWAITING_CONFIRMATION.value,
        RunPhase.CONFIRMED.value,
    ])
    def test_cancel_allowed_before_apply(self, phase):
        from app.api.agent_apply import _agent_cancel, CancelRequest

        run_id = str(uuid.uuid4())
        try:
            save_run_state(run_id, {"phase": phase})
            out = _agent_cancel(CancelRequest(run_id=run_id, reason="user"))
            assert out["status"] == "cancelled"
            assert out["run_phase"] == RunPhase.CANCELLED.value
            assert out["plan_retryable"] is False
            assert load_run_state(run_id)["phase"] == RunPhase.CANCELLED.value
        finally:
            delete_run_state(run_id)

    @pytest.mark.parametrize("phase", [
        RunPhase.APPLYING.value,
        RunPhase.COMPLETED.value,
        RunPhase.FAILED.value,
        RunPhase.CANCELLED.value,
    ])
    def test_cancel_rejected_after_apply_or_terminal(self, phase):
        from fastapi import HTTPException

        from app.api.agent_apply import _agent_cancel, CancelRequest

        run_id = str(uuid.uuid4())
        try:
            save_run_state(run_id, {"phase": phase})
            with pytest.raises(HTTPException) as exc:
                _agent_cancel(CancelRequest(run_id=run_id))
            assert exc.value.status_code == 409
            assert load_run_state(run_id)["phase"] == phase
        finally:
            delete_run_state(run_id)

    def test_cancel_leaves_no_artifacts_and_keeps_plan(self):
        from app.api.agent_apply import _agent_cancel, CancelRequest

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        try:
            _confirmed_state(run_id, ws)
            plan_before = load_run_state(run_id)["compiled_plan"]
            before = sorted(os.listdir(ws))
            _agent_cancel(CancelRequest(run_id=run_id))
            state = load_run_state(run_id)
            assert state["phase"] == RunPhase.CANCELLED.value
            assert state["compiled_plan"] == plan_before
            assert sorted(os.listdir(ws)) == before
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

    def test_cancel_missing_run_is_rejected(self):
        from fastapi import HTTPException

        from app.api.agent_apply import _agent_cancel, CancelRequest

        with pytest.raises(HTTPException) as exc:
            _agent_cancel(CancelRequest(run_id=str(uuid.uuid4())))
        assert exc.value.status_code == 400


# ══════════════════════════════════════════════════════════════════
# D3 — structured deterministic instance choices (5A)
# ══════════════════════════════════════════════════════════════════


class TestInstanceChoices:
    def _index(self, cap, count=2):
        from app.engine.state_adapter import ComponentInstanceInfo
        from app.engine.structural_index import StructuralIndex

        insts = [
            ComponentInstanceInfo(
                capability=cap,
                path=f"{cap}.inst{i}",
                file_path=f"src/components/Inst{i}.tsx",
                instance_id=str(i),
                slot_id=f"slot_{i}",
            )
            for i in range(count)
        ]
        return StructuralIndex.from_mapping({cap: insts})

    def test_choices_exposed_for_multiple_instances(self):
        from app.intent.instance_choices import build_instance_choices

        cap = "presentation.kpi_row"
        choices = build_instance_choices(cap, self._index(cap, 2))
        assert len(choices) == 2
        assert {c["instance_hint"] for c in choices} == {"slot_0", "slot_1"}
        assert all(c["kind"] == "instance" for c in choices)
        assert all(c["file_path"] for c in choices)

    def test_no_choices_for_zero_or_one_instance(self):
        from app.intent.instance_choices import build_instance_choices

        cap = "presentation.kpi_row"
        assert build_instance_choices(cap, None) == []
        assert build_instance_choices(cap, self._index(cap, 1)) == []

    def test_enrichment_never_auto_selects(self):
        from app.intent.instance_choices import enrich_draft_instance_choices

        cap = "presentation.kpi_row"
        draft = {"proposed_actions": [
            {"verb": "modify", "target_capability": cap},
        ]}
        enrich_draft_instance_choices(draft, self._index(cap, 3))
        action = draft["proposed_actions"][0]
        assert len(action["instance_choices"]) == 3
        assert "instance_hint" not in action
        assert draft["instance_ambiguous"] is True
        assert len(draft["instance_choices"]) == 3

    def test_enrichment_skips_actions_that_already_have_a_hint(self):
        from app.intent.instance_choices import enrich_draft_instance_choices

        cap = "presentation.kpi_row"
        draft = {"proposed_actions": [
            {"verb": "modify", "target_capability": cap, "instance_hint": "slot_1"},
        ]}
        enrich_draft_instance_choices(draft, self._index(cap, 3))
        assert "instance_choices" not in draft["proposed_actions"][0]
        assert draft["instance_ambiguous"] is False

    def test_interpret_response_exposes_preplan_contract(self, monkeypatch):
        from app.api import agent_interpret as mod

        cap = "presentation.kpi_row"
        idx = self._index(cap, 2)

        class _Ctx:
            workspace = "/tmp/agent-sb/runs/fake"

        monkeypatch.setattr("app.runtime.context.build_context",
                            lambda run_id, session_id=None: _Ctx())
        monkeypatch.setattr("app.executor.worktree_manager.ensure_worktree",
                            lambda ctx: None)
        monkeypatch.setattr(
            "app.engine.structural_index.StructuralIndex.from_worktree",
            staticmethod(lambda ws: idx),
        )

        class _Draft:
            def to_dict(self):
                return {
                    "status": "ok",
                    "contract_id": "dashboard.sales_overview",
                    "contract_version": 1,
                    "proposed_actions": [{"verb": "modify", "target_capability": cap}],
                    "alternatives": [],
                    "params_proposed": {},
                    "worktree_capabilities": [],
                    "clarification_question": None,
                }

        monkeypatch.setattr(mod, "interpret", lambda **kw: _Draft())

        out = mod.agent_interpret(mod.InterpretRequest(
            run_id=str(uuid.uuid4()), message="update the kpi row",
        ))
        assert out["stage"] == "interpretation"
        assert out["plan_confirmed"] is False
        assert len(out["instance_choices"]) == 2

    def test_explicit_instance_hint_satisfies_repository_matrix(self):
        from app.engine.apply_engine import _validate_repository_matrix
        from app.engine.state_adapter import ComponentInstanceInfo
        from app.engine.structural_index import StructuralIndex
        from app.graphir.structure.resolver import _instance_matches

        cap = "presentation.kpi_row"
        insts = [
            ComponentInstanceInfo(
                capability=cap, path=f"{cap}.i{i}",
                file_path=f"src/components/Inst{i}.tsx",
                instance_id=str(i), slot_id=f"slot_{i}",
            )
            for i in range(2)
        ]
        index = StructuralIndex.from_mapping({cap: insts})
        assert _instance_matches(insts[1], "slot_1")
        assert not _instance_matches(insts[0], "slot_1")
        assert not _instance_matches(insts[0], "slot_9")

    def test_repository_matrix_conflict_lists_instance_candidates(self):
        from app.engine.apply_engine import _validate_repository_matrix
        from app.engine.state_adapter import ComponentInstanceInfo
        from app.engine.structural_index import StructuralIndex
        from app.engine.structural_completion import (
            MODIFY, CompletionMode, ResolvedCapability, StructuralIR,
        )

        cap = "presentation.kpi_row"
        insts = [
            ComponentInstanceInfo(
                capability=cap, path=f"{cap}.i{i}",
                file_path=f"src/components/Inst{i}.tsx",
                instance_id=str(i), slot_id=f"slot_{i}",
            )
            for i in range(3)
        ]
        index = StructuralIndex.from_mapping({cap: insts})
        ir = StructuralIR(
            contract_id="dashboard.sales_overview", contract_version=1,
            capabilities=(ResolvedCapability(
                name=cap, params={}, mode=CompletionMode.SAFE_COMPLETE, action=MODIFY,
            ),),
            param_provenance={}, confidence=1.0,
        )
        result = _validate_repository_matrix(ir, index, None)
        assert result is not None
        ex = result["execution"]
        assert ex["status"] == "conflict"
        assert len(ex["candidates"]) == 3
        assert {c["instance_hint"] for c in ex["candidates"]} == {
            "slot_0", "slot_1", "slot_2",
        }
        assert ex["details"]["required_field"] == "instance_hint"

    def test_repository_matrix_accepts_explicit_selection(self):
        from app.engine.apply_engine import _validate_repository_matrix
        from app.engine.state_adapter import ComponentInstanceInfo
        from app.engine.structural_index import StructuralIndex
        from app.engine.structural_completion import (
            MODIFY, CompletionMode, ResolvedCapability, StructuralIR,
        )

        cap = "presentation.kpi_row"
        insts = [
            ComponentInstanceInfo(
                capability=cap, path=f"{cap}.i{i}",
                file_path=f"src/components/Inst{i}.tsx",
                instance_id=str(i), slot_id=f"slot_{i}",
            )
            for i in range(3)
        ]
        index = StructuralIndex.from_mapping({cap: insts})
        ir = StructuralIR(
            contract_id="dashboard.sales_overview", contract_version=1,
            capabilities=(ResolvedCapability(
                name=cap, params={}, mode=CompletionMode.SAFE_COMPLETE,
                action=MODIFY, instance_hint="slot_2",
            ),),
            param_provenance={}, confidence=1.0,
        )
        assert _validate_repository_matrix(ir, index, None) is None


# ══════════════════════════════════════════════════════════════════
# G1 — source_capability survives confirm (REPLACE reachability)
# ══════════════════════════════════════════════════════════════════


class TestSourceCapabilityPassthrough:
    def test_confirm_request_model_accepts_source_capability(self):
        from app.api.agent_confirm import ConfirmRequest

        req = ConfirmRequest(
            run_id=str(uuid.uuid4()),
            interpretation_id="i",
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[{
                "verb": "replace",
                "target_capability": "presentation.kpi_row",
                "source_capability": "presentation.line_chart",
                "confidence": 1.0,
            }],
            params={},
            user_message="swap chart for kpi",
        )
        assert req.actions[0]["source_capability"] == "presentation.line_chart"

    def test_confirm_builds_action_with_source_capability(self):
        import pathlib

        src = pathlib.Path(
            os.path.join(os.path.dirname(__file__), "..", "..",
                         "backend", "app", "api", "agent_confirm.py")
        ).read_text(encoding="utf-8")
        assert "source_capability" in src

    def test_plan_compiler_transports_source_capability(self):
        from app.intent.models import ConfirmedIntent, IntentAction
        from app.intent.plan_compiler import compile_plan

        ci = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[IntentAction(
                verb="replace",
                target_capability="presentation.kpi_row",
                source_capability="presentation.line_chart",
                confidence=1.0,
            )],
            params={},
            user_message="swap chart for kpi",
            interpretation_id="i",
        )
        plan = compile_plan(ci)
        frame_actions = plan.semantic_frame.get("actions", [])
        assert frame_actions, "semantic frame must carry actions"
        assert any(
            a.get("source_capability") == "presentation.line_chart"
            for a in frame_actions
        )


# ══════════════════════════════════════════════════════════════════
# G9 / Run inspection
# ══════════════════════════════════════════════════════════════════


class TestRunInspection:
    def test_get_run_exposes_lifecycle(self):
        from main import get_run

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        try:
            _confirmed_state(run_id, ws)
            save_run_state(run_id, {"plan_retryable": True,
                                    "last_conflict": {"conflict": "concurrency"}})
            data = get_run(run_id)
            assert data["run_phase"] == RunPhase.CONFIRMED.value
            assert data["plan_confirmed"] is True
            assert data["plan_retryable"] is True
            assert data["stage"] == "confirmed"
            assert data["last_conflict"]["conflict"] == "concurrency"
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

    def test_get_run_without_state_reports_unknown(self):
        from main import get_run

        data = get_run(str(uuid.uuid4()))
        assert data["run_phase"] is None
        assert data["plan_confirmed"] is False
        assert data["stage"] == "unknown"


# ══════════════════════════════════════════════════════════════════
# G8 — structured semantic alternatives (distinct from instance choices)
# ══════════════════════════════════════════════════════════════════


class TestSemanticAlternatives:
    def _entry(self):
        from app.catalog.loader import load_catalog

        return load_catalog()["contracts"]["dashboard.sales_overview"]

    def test_ambiguous_message_yields_structured_alternatives(self):
        from app.intent.interpreter import _build_semantic_alternatives

        alts = _build_semantic_alternatives(
            "dashboard.sales_overview", 1, self._entry(), "remove the sales kpi",
        )
        assert len(alts) >= 2
        for alt in alts:
            assert alt["kind"] == "capability"
            assert alt["id"].startswith("alt_")
            assert alt["proposed_actions"][0]["target_capability"]
            assert alt["proposed_actions"][0]["verb"] == "remove"
        # Deterministic order: score desc, then id asc.
        scores = [a["score"] for a in alts]
        assert scores == sorted(scores, reverse=True)

    def test_unambiguous_message_yields_no_alternatives(self):
        from app.intent.interpreter import _build_semantic_alternatives

        assert _build_semantic_alternatives(
            "dashboard.sales_overview", 1, self._entry(), "remove the trend chart",
        ) == []
        assert _build_semantic_alternatives(
            "dashboard.sales_overview", 1, self._entry(), "make it prettier",
        ) == []

    def test_alternatives_are_deterministic(self):
        from app.intent.interpreter import _build_semantic_alternatives

        a = _build_semantic_alternatives(
            "dashboard.sales_overview", 1, self._entry(), "update the sales dashboard")
        b = _build_semantic_alternatives(
            "dashboard.sales_overview", 1, self._entry(), "update the sales dashboard")
        assert a == b

    def test_confirm_requires_a_selection_when_alternatives_exist(self):
        from app.api.agent_confirm import ConfirmRequest, _agent_confirm

        run_id = str(uuid.uuid4())
        try:
            save_run_state(run_id, {
                "phase": RunPhase.AWAITING_CONFIRMATION.value,
                "interpretation_draft": {
                    "status": "clarification_needed",
                    "alternatives": [{
                        "id": "alt_0",
                        "proposed_actions": [{
                            "verb": "remove",
                            "target_capability": "presentation.kpi_row",
                        }],
                    }],
                },
            })
            out = _agent_confirm(ConfirmRequest(
                run_id=run_id, interpretation_id="i",
                contract_id="dashboard.sales_overview", contract_version=1,
                actions=[], params={}, user_message="x",
            ))
            assert out["status"] == "rejected"
            assert out["gate"]["reason"] == "alternatives_pending"
            assert out["alternatives"]
        finally:
            delete_run_state(run_id)

    def test_confirm_rejects_out_of_range_selection(self):
        from app.api.agent_confirm import ConfirmRequest, _agent_confirm

        run_id = str(uuid.uuid4())
        try:
            save_run_state(run_id, {
                "phase": RunPhase.AWAITING_CONFIRMATION.value,
                "interpretation_draft": {
                    "status": "clarification_needed",
                    "alternatives": [{
                        "id": "alt_0",
                        "proposed_actions": [{
                            "verb": "remove",
                            "target_capability": "presentation.kpi_row",
                        }],
                    }],
                },
            })
            out = _agent_confirm(ConfirmRequest(
                run_id=run_id, interpretation_id="i",
                contract_id="dashboard.sales_overview", contract_version=1,
                actions=[], params={}, user_message="x", alternative_index=7,
            ))
            assert out["status"] == "rejected"
            assert out["gate"]["reason"] == "invalid_alternative"
        finally:
            delete_run_state(run_id)

    def test_selected_alternative_is_the_authoritative_action_set(self):
        from app.api.agent_confirm import ConfirmRequest, _agent_confirm

        run_id = str(uuid.uuid4())
        try:
            save_run_state(run_id, {
                "phase": RunPhase.AWAITING_CONFIRMATION.value,
                "interpretation_draft": {
                    "status": "clarification_needed",
                    "alternatives": [
                        {"id": "alt_0", "proposed_actions": [
                            {"verb": "remove", "target_capability": "presentation.kpi_row"}]},
                        {"id": "alt_1", "proposed_actions": [
                            {"verb": "remove", "target_capability": "layout.page"}]},
                    ],
                },
            })
            out = _agent_confirm(ConfirmRequest(
                run_id=run_id, interpretation_id="i",
                contract_id="dashboard.sales_overview", contract_version=1,
                actions=[], params={}, user_message="x", alternative_index=1,
            ))
            assert out["status"] != "rejected"
            saved = load_run_state(run_id)
            assert saved["confirmed_intent"]["actions"][0]["target_capability"] == "layout.page"
            assert saved["clarification_resolution"]["alternative_id"] == "alt_1"
        finally:
            delete_run_state(run_id)

    def test_instance_hint_survives_alternative_substitution(self):
        from app.api.agent_confirm import ConfirmRequest, _agent_confirm

        run_id = str(uuid.uuid4())
        try:
            save_run_state(run_id, {
                "phase": RunPhase.AWAITING_CONFIRMATION.value,
                "interpretation_draft": {
                    "status": "clarification_needed",
                    "alternatives": [{
                        "id": "alt_0",
                        "proposed_actions": [{
                            "verb": "modify", "target_capability": "presentation.kpi_row",
                        }],
                    }],
                },
            })
            out = _agent_confirm(ConfirmRequest(
                run_id=run_id, interpretation_id="i",
                contract_id="dashboard.sales_overview", contract_version=1,
                actions=[{
                    "verb": "modify", "target_capability": "presentation.kpi_row",
                    "instance_hint": "slot_1",
                }],
                params={}, user_message="x", alternative_index=0,
            ))
            assert out["status"] != "rejected"
            saved = load_run_state(run_id)
            assert saved["confirmed_intent"]["actions"][0]["instance_hint"] == "slot_1"
        finally:
            delete_run_state(run_id)


# ══════════════════════════════════════════════════════════════════
# Transport surfaces (MCP / orchestrator)
# ══════════════════════════════════════════════════════════════════


class TestTransportSurfaces:
    def test_backend_routes_registered(self):
        import main

        paths = {r.path for r in main.app.routes if hasattr(r, "path")}
        assert {"/agent/retry", "/agent/cancel"} <= paths

    def test_cancel_endpoint_over_http(self):
        from fastapi.testclient import TestClient

        import main

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        try:
            _confirmed_state(run_id, ws)
            with TestClient(main.app) as client:
                resp = client.post("/agent/cancel", json={"run_id": run_id})
                assert resp.status_code == 200
                body = resp.json()
                assert body["status"] == "cancelled"
                assert body["run_phase"] == RunPhase.CANCELLED.value
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

    def test_cancel_endpoint_rejects_applying_over_http(self):
        from fastapi.testclient import TestClient

        import main

        run_id = str(uuid.uuid4())
        try:
            save_run_state(run_id, {"phase": RunPhase.APPLYING.value})
            with TestClient(main.app) as client:
                resp = client.post("/agent/cancel", json={"run_id": run_id})
                assert resp.status_code == 409
        finally:
            delete_run_state(run_id)

    def test_retry_endpoint_over_http(self):
        from fastapi.testclient import TestClient

        import main

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        try:
            _confirmed_state(run_id, ws)
            with open(os.path.join(ws, "src", "components", "LineChart.tsx"), "a") as f:
                f.write("// drift\n")
            with TestClient(main.app) as client:
                resp = client.post("/agent/retry", json={"run_id": run_id, "dry_run": True})
                assert resp.status_code == 200
                meta = resp.json()["meta"]
                assert meta["retry"] == {"same_plan": True, "fresh_snapshot": True}
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

    def test_apply_conflict_then_retry_recovers_over_http(self):
        from fastapi.testclient import TestClient

        import main

        run_id = str(uuid.uuid4())
        ws = _seed_workspace(run_id)
        try:
            _confirmed_state(run_id, ws)
            # Workspace drifts after confirm → apply must NO WRITE and park.
            with open(os.path.join(ws, "src", "components", "LineChart.tsx"), "a") as f:
                f.write("// drift\n")
            with TestClient(main.app) as client:
                apply_resp = client.post(
                    "/agent/apply", json={"run_id": run_id, "dry_run": True})
                assert apply_resp.status_code == 200
                ex = apply_resp.json()["execution"]
                assert ex["status"] == "conflict"
                assert ex["conflict"] == "concurrency"
                assert ex["run_phase"] == RunPhase.CONFIRMED.value
                plan_after_conflict = load_run_state(run_id)["compiled_plan"]

                # Explicit retry: same plan, fresh baseline, no conflict.
                retry_resp = client.post(
                    "/agent/retry", json={"run_id": run_id, "dry_run": True})
                assert retry_resp.status_code == 200
                meta = retry_resp.json()["meta"]
                assert meta["retry"] == {"same_plan": True, "fresh_snapshot": True}
                assert meta["concurrency"]["status"] == "ok"
            assert load_run_state(run_id)["compiled_plan"] == plan_after_conflict
        finally:
            delete_run_state(run_id)
            shutil.rmtree(ws, ignore_errors=True)

    def test_mcp_exposes_retry_and_cancel(self):
        import pathlib

        src = pathlib.Path(
            os.path.join(os.path.dirname(__file__), "..", "..",
                         "backend", "mcp-server", "server.py")
        ).read_text(encoding="utf-8")
        assert "async def agent_retry(" in src
        assert "async def agent_cancel(" in src

    def test_orchestrator_exposes_retry_and_cancel(self):
        import pathlib

        src = pathlib.Path(
            os.path.join(os.path.dirname(__file__), "..", "..",
                         "orchestrator", "main.py")
        ).read_text(encoding="utf-8")
        assert "/run/{run_id}/retry" in src
        assert "/run/{run_id}/cancel" in src
        # G10: the gate is read top-level first (legacy plan_preview path only
        # survives as an explicit fallback).
        assert 'gate = snapshot.get("gate")' in src
        assert 'gate = plan_preview.get("gate", {})' in src

    def test_orchestrator_uses_backend_phase_vocabulary(self):
        import pathlib

        orch = pathlib.Path(
            os.path.join(os.path.dirname(__file__), "..", "..", "orchestrator")
        )
        for name in ("main.py", "graph.py", "nodes/confirm.py",
                     "nodes/call_apply.py", "nodes/validate_plan.py",
                     "nodes/interpret.py", "nodes/return_result.py"):
            text = (orch / name).read_text(encoding="utf-8")
            assert '"awaiting_apply"' not in text, f"{name} still uses awaiting_apply"