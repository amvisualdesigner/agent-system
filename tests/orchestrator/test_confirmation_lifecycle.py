"""Confirmation lifecycle — behavioral tests.

Bloquea el contrato restaurado por `fix(flow): restore confirmation lifecycle
and binding decisions`:

  interpret → awaiting_confirmation → confirm → confirmed → apply → completed

Reglas:
  * una clarificación resoluble (binding sin elegir / G6) re-aparea en
    `awaiting_confirmation`, nunca en `failed` ni en `completed`;
  * `confirmed` (Plan confirmado, Apply sin ejecutar) NUNCA se settle como
    `completed` y no inventa `execution`;
  * el guard de Apply sigue exigiendo `confirmed`;
  * un rechazo técnico real de confirmación SÍ sigue produciendo `failed`.
"""

import asyncio
import importlib.util
import pathlib
import sys
import types
import uuid

import pytest

from fastapi import HTTPException

import nodes.confirm as confirm_mod
import nodes.interpret as interpret_mod
import nodes.call_apply as call_apply_mod
import nodes.return_result as return_result_mod
from nodes.interpret import interpret_node
from nodes.confirm import confirm_node
from nodes.validate_plan import validate_plan_node
from nodes.call_apply import call_apply_node
from nodes.return_result import return_result_node
from sse import emitter

ORCH = pathlib.Path(__file__).resolve().parents[2] / "orchestrator"


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def capture_events(monkeypatch):
    events = []

    async def _emit(run_id, event):
        events.append(event)

    monkeypatch.setattr(emitter, "emit", _emit)
    return events


@pytest.fixture(autouse=True)
def capture_snapshots(monkeypatch):
    saved = []

    def _save(run_id, snapshot):
        saved.append(dict(snapshot))
        return "/nonexistent/test-snapshot.json"

    monkeypatch.setattr(return_result_mod, "save_snapshot", _save)
    return saved


@pytest.fixture(scope="module")
def orch_main():
    """Import orchestrator/main.py with a stubbed compiled graph.

    langgraph is not required for these tests: the guard paths under test
    raise before any graph execution.
    """
    saved_graph = sys.modules.get("graph")
    fake_graph = types.ModuleType("graph")

    class _Compiled:
        async def ainvoke(self, state):
            return state

    fake_graph.compiled_graph = _Compiled()
    sys.modules["graph"] = fake_graph
    try:
        spec = importlib.util.spec_from_file_location(
            "orchestrator_main_under_test", ORCH / "main.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved_graph is None:
            sys.modules.pop("graph", None)
        else:
            sys.modules["graph"] = saved_graph
    return mod


# ── helpers ───────────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def _state(**over):
    state = {
        "task": "Add a filter by size in sales dashboard",
        "run_id": str(uuid.uuid4()),
        "session_id": None,
        "plan": None,
        "execution": None,
        "run_details": None,
        "error": None,
        "trace": [],
        "phase": "interpreting",
        "cancelled": False,
        "backend_run_id": str(uuid.uuid4()),
        "planner_meta": None,
        "_next_node": None,
        "start_node": "interpret",
        "interpretation": None,
        "confirmed_intent": None,
        "plan_preview": None,
    }
    state.update(over)
    return state


HOOK_CANDIDATE = {
    "kind": "hook",
    "ref": "useDashboardData",
    "selector": None,
    "schema": {
        "ref": "hook:useDashboardData.result",
        "shape": "object",
        "fields": ["tableData"],
    },
    "mappings": [],
}

SLICE_CANDIDATE = {
    "kind": "slice",
    "ref": "filters",
    "selector": "filters",
    "schema": {
        "ref": "registry:Page.slices[FilterPanel.filters]",
        "shape": "object",
        "fields": ["filters"],
    },
    "mappings": [
        {
            "prop": "filters",
            "stage": "slice",
            "value_path": "_pageData.filters",
            "transform": "identity",
            "shape": "array<string>",
        }
    ],
}


def _binding_proposal(status="needs_choice"):
    return {
        "status": status,
        "target_component": "FilterPanel",
        "source": None,
        "schema": None,
        "mapping": [],
        "evidence": [],
        "provenance": {"candidates": [HOOK_CANDIDATE, SLICE_CANDIDATE]},
    }


def _draft(with_binding=True):
    action = {
        "verb": "create",
        "target_capability": "presentation.filter_panel",
        "label": "FilterPanel",
        "confidence": 0.9,
    }
    if with_binding:
        action["binding_proposal"] = _binding_proposal()
    return {
        "status": "ok",
        "stage": "interpretation",
        "plan_confirmed": False,
        "contract_id": "dashboard",
        "contract_version": 1,
        "proposed_actions": [dict(action)],
        "params_proposed": {},
        "binding_proposals": (
            [{"target_capability": "presentation.filter_panel",
              "target_component": "FilterPanel", "verb": "create",
              "status": "needs_choice"}]
            if with_binding else []
        ),
    }


def _selected_binding():
    """Selected candidate in the exact wire format /agent/confirm validates."""
    return {
        "source": {"kind": "slice", "ref": "filters", "selector": "filters"},
        "schema": SLICE_CANDIDATE["schema"],
        "mapping": [
            {"prop": "filters", "from_field": "_pageData.filters",
             "transform": "identity"}
        ],
    }


def _confirmed_intent(actions):
    return {
        "contract_id": "dashboard",
        "contract_version": 1,
        "actions": actions,
        "params": {},
        "user_message": "Add a filter by size in sales dashboard",
    }


def _interpret(monkeypatch, draft=None):
    draft = draft if draft is not None else _draft()

    async def _fake_interpret(task, run_id="", session_id=None):
        return draft

    monkeypatch.setattr(interpret_mod, "call_interpret", _fake_interpret)
    return _run(interpret_node(_state()))


def _confirm(monkeypatch, state, confirm_result=None, exc=None):
    if exc is not None:
        async def _fake_confirm(payload):
            raise exc
    else:
        captured = []

        async def _fake_confirm(payload):
            captured.append(payload)
            return confirm_result

        _confirm.captured = captured
    monkeypatch.setattr(confirm_mod, "call_confirm", _fake_confirm)
    return _run(confirm_node(state))


def _apply(monkeypatch, state, apply_result):
    async def _fake_apply(run_id, plan, confirmed_deletions=None):
        return apply_result

    monkeypatch.setattr(call_apply_mod, "backend_call_apply", _fake_apply)
    return _run(call_apply_node(state))


APPLY_OK = {
    "execution": {"status": "ok", "operations": [
        {"action": "create", "path": "frontend/src/pages/dashboard/FilterPanel.tsx"}
    ]},
    "context": {"repo_snapshot": [
        "frontend/src/pages/dashboard/FilterPanel.tsx"
    ]},
    "meta": {"verify": {"status": "passed"}},
}

CONFIRM_OK = {
    "status": "ok",
    "plan": {"actions": [{"verb": "create",
                          "target_capability": "presentation.filter_panel"}],
             "contract_id": "dashboard",
             "skill_ir": {"contract_id": "dashboard", "confidence": 0.9}},
    "plan_preview": {"summary_human": "Crear FilterPanel"},
    "gate": {"blocked": False, "reason": None},
}

BINDING_REJECTED = {
    "status": "rejected",
    "reason": ("Clarification required: choose one of the 2 candidate data "
               "sources for 'presentation.filter_panel' via action.binding."),
    "gate": {"blocked": True, "reason": "binding_choice_pending"},
    "binding_error": "binding_choice_pending",
    "binding_proposal": _binding_proposal(),
}


def _result_event(events, run_id=None):
    for ev in events:
        if ev.type == "result" and (run_id is None or ev.run_id == run_id):
            return ev
    return None


# ── A. interpret → confirm → confirmed → apply → completed ───────────────


class TestNormalConfirmationFlow:
    def test_interpret_parks_in_awaiting_confirmation(self, monkeypatch,
                                                      capture_events):
        state = _interpret(monkeypatch)
        assert state["phase"] == "awaiting_confirmation"
        assert state["_next_node"] == "return_result"
        assert state["interpretation"]["binding_proposals"][0]["status"] == "needs_choice"
        ready = [e for e in capture_events if e.type == "interpretation_ready"]
        assert ready and ready[0].phase == "awaiting_confirmation"

    def test_full_flow_settles_in_completed_only_after_apply(
            self, monkeypatch, capture_events, capture_snapshots):
        state = _interpret(monkeypatch)
        state["confirmed_intent"] = _confirmed_intent([dict(
            state["interpretation"]["proposed_actions"][0])])

        state = _confirm(monkeypatch, state, CONFIRM_OK)
        assert state["phase"] == "confirmed"
        assert state["_next_node"] == "validate_plan"

        state = _run(validate_plan_node(state))
        assert state["phase"] == "confirmed"

        parked = _run(return_result_node(state))
        assert parked["phase"] == "confirmed"
        snap = capture_snapshots[-1]
        assert snap["phase"] == "confirmed"
        assert snap["status"] == "confirmed"
        ev = _result_event(capture_events, state["run_id"])
        assert ev.phase == "confirmed"
        assert ev.data["status"] == "confirmed"

        # Apply (explicito, endpoint) → call_apply → completed
        state = _apply(
            monkeypatch,
            {**parked, "phase": "applying", "start_node": "apply",
             "execution": None},
            APPLY_OK,
        )
        assert state["phase"] == "applying"
        done = _run(return_result_node(state))
        assert done["phase"] == "completed"
        snap = capture_snapshots[-1]
        assert snap["status"] == "ok"
        assert snap["execution"]["status"] == "ok"


# ── B. binding clarification parks and re-confirms ───────────────────────


class TestBindingClarification:
    def _parked_state(self):
        draft = _draft()
        state = _state(
            phase="awaiting_confirmation",
            start_node="confirm",
            interpretation=draft,
            confirmed_intent=_confirmed_intent([dict(draft["proposed_actions"][0])]),
        )
        return state

    def test_confirm_without_binding_parks_in_awaiting_confirmation(
            self, monkeypatch, capture_events, capture_snapshots):
        state = self._parked_state()
        out = _confirm(monkeypatch, state, BINDING_REJECTED)

        assert out["phase"] == "awaiting_confirmation"
        assert out["error"] is None
        assert out["_next_node"] == "return_result"
        assert "Clarification required" in out["interpretation"]["clarification_reason"]
        assert out["interpretation"]["binding_error"] == "binding_choice_pending"
        assert out["interpretation"]["binding_rejected_proposal"]["status"] == "needs_choice"

        # El evento re-emitido lleva la decisión pendiente para la UI
        ready = [e for e in capture_events
                 if e.type == "interpretation_ready" and e.run_id == out["run_id"]]
        assert ready, "clarification must re-emit interpretation_ready"
        assert ready[-1].data["binding_rejected_proposal"]["status"] == "needs_choice"
        assert ready[-1].phase == "awaiting_confirmation"

        # El resultado settlea como parked, NUNCA failed
        _run(return_result_node(out))
        snap = capture_snapshots[-1]
        assert snap["phase"] == "awaiting_confirmation"
        assert snap["status"] == "awaiting_confirmation"
        assert snap["error"] is None

    def test_selected_binding_reconfirms(self, monkeypatch):
        draft = _draft()
        action = dict(draft["proposed_actions"][0])
        action["binding"] = _selected_binding()
        state = _state(
            phase="awaiting_confirmation",
            start_node="confirm",
            interpretation=draft,
            confirmed_intent=_confirmed_intent([action]),
        )
        out = _confirm(monkeypatch, state, CONFIRM_OK)
        assert out["phase"] == "confirmed"
        assert out["error"] is None
        # El binding seleccionado cruza íntegro hacia /agent/confirm
        payload = _confirm.captured[-1]
        assert payload["actions"][0]["binding"]["source"]["ref"] == "filters"
        assert payload["actions"][0]["binding"]["mapping"][0]["from_field"] == "_pageData.filters"


# ── C. confirmed ≠ completed ─────────────────────────────────────────────


class TestConfirmedIsNotCompleted:
    def test_confirmed_result_has_no_invented_execution(
            self, monkeypatch, capture_events, capture_snapshots):
        draft = _draft(with_binding=False)
        state = _state(
            phase="awaiting_confirmation",
            start_node="confirm",
            interpretation=draft,
            confirmed_intent=_confirmed_intent([dict(draft["proposed_actions"][0])]),
        )
        state = _confirm(monkeypatch, state, CONFIRM_OK)
        state = _run(validate_plan_node(state))
        assert state["phase"] == "confirmed"

        _run(return_result_node(state))
        snap = capture_snapshots[-1]
        assert snap["status"] == "confirmed"
        assert snap["phase"] == "confirmed"
        assert snap["execution"] is None
        assert snap["error"] is None

        ev = _result_event(capture_events, state["run_id"])
        assert ev.type == "result"
        assert ev.data["status"] == "confirmed"
        assert not ev.data.get("execution")

    def test_apply_after_confirmed_result_still_works(
            self, monkeypatch, capture_snapshots):
        draft = _draft(with_binding=False)
        state = _state(
            phase="awaiting_confirmation",
            start_node="confirm",
            interpretation=draft,
            confirmed_intent=_confirmed_intent([dict(draft["proposed_actions"][0])]),
        )
        state = _confirm(monkeypatch, state, CONFIRM_OK)
        state = _run(validate_plan_node(state))
        parked = _run(return_result_node(state))
        assert parked["phase"] == "confirmed"

        state2 = {**parked, "phase": "applying", "start_node": "apply",
                  "execution": None}
        state2 = _apply(monkeypatch, state2, APPLY_OK)
        done = _run(return_result_node(state2))
        assert done["phase"] == "completed"


# ── D. apply guard ───────────────────────────────────────────────────────


class TestApplyGuard:
    @pytest.mark.parametrize("phase", [
        "awaiting_confirmation", "interpreting", "failed", "completed",
    ])
    def test_apply_rejected_before_confirmed(self, orch_main, monkeypatch, phase):
        run_id = str(uuid.uuid4())
        monkeypatch.setattr(
            orch_main, "load_snapshot",
            lambda rid: {"run_id": rid, "phase": phase, "task": "t"},
        )
        with pytest.raises(HTTPException) as exc:
            asyncio.run(orch_main.apply_run(run_id, orch_main.ApplyRequest()))
        assert exc.value.status_code == 400
        assert "Expected 'confirmed'" in exc.value.detail

    def test_apply_unknown_run_404(self, orch_main, monkeypatch):
        run_id = str(uuid.uuid4())
        monkeypatch.setattr(orch_main, "load_snapshot", lambda rid: None)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(orch_main.apply_run(run_id, orch_main.ApplyRequest()))
        assert exc.value.status_code == 404


# ── E. technical failure stays failed ─────────────────────────────────────


class TestTechnicalFailure:
    def test_non_clarification_rejection_is_terminal_failed(
            self, monkeypatch, capture_snapshots):
        draft = _draft(with_binding=False)
        state = _state(
            phase="awaiting_confirmation",
            start_node="confirm",
            interpretation=draft,
            confirmed_intent=_confirmed_intent([dict(draft["proposed_actions"][0])]),
        )
        out = _confirm(monkeypatch, state, {
            "status": "rejected",
            "reason": "Contract 'dashboard' not found",
        })
        assert out["phase"] == "failed"
        assert out["error"] == "Contract 'dashboard' not found"

        _run(return_result_node(out))
        snap = capture_snapshots[-1]
        assert snap["phase"] == "failed"
        assert snap["status"] == "failed"
        assert snap["error"] == "Contract 'dashboard' not found"

    def test_transport_exception_is_terminal_failed(
            self, monkeypatch, capture_snapshots):
        draft = _draft(with_binding=False)
        state = _state(
            phase="awaiting_confirmation",
            start_node="confirm",
            interpretation=draft,
            confirmed_intent=_confirmed_intent([dict(draft["proposed_actions"][0])]),
        )
        out = _confirm(monkeypatch, state, exc=RuntimeError("connection refused"))
        assert out["phase"] == "failed"
        assert "connection refused" in out["error"]

        _run(return_result_node(out))
        assert capture_snapshots[-1]["status"] == "failed"


# ── G6 guard stays parked and hands the decision back ────────────────────


class TestG6GuardParks:
    def test_blocked_clarification_parks_and_reemits_decision(
            self, monkeypatch, capture_events, capture_snapshots):
        draft = _draft(with_binding=False)
        draft["status"] = "clarification_needed"
        draft["choices"] = [{"id": "sales", "page": "Sales Dashboard"}]
        draft["clarification_question"] = "Which page?"
        state = _state(
            phase="awaiting_confirmation",
            start_node="confirm",
            interpretation=draft,
            confirmed_intent=_confirmed_intent([dict(draft["proposed_actions"][0])]),
        )
        out = _confirm(monkeypatch, state, CONFIRM_OK)  # backend never called
        assert out["phase"] == "awaiting_confirmation"
        assert out["error"] is None
        ready = [e for e in capture_events if e.type == "interpretation_ready"]
        assert ready and ready[-1].data["clarification_reason"]
        assert "page context" in ready[-1].data["clarification_reason"]

        _run(return_result_node(out))
        assert capture_snapshots[-1]["status"] == "awaiting_confirmation"
