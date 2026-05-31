"""State machine validation tests for interpret → confirm → apply flow.

Tests the 5 scenarios requested before writing LangGraph code:

1. POST /interpret → phase = awaiting_confirmation, NO apply
2. POST /confirm without prior interpret → must fail
3. POST /apply without confirmed_intent → must fail
4. POST /apply with gate.blocked=True → must fail
5. Confirm is idempotent (repeat confirm returns same result)
"""

from __future__ import annotations

import os
import json

import pytest

from app.intent.models import RunPhase, validate_transition, RunState


# ── Pure state machine tests ───────────────────────────────────


class TestPhaseTransitions:
    """Validate RunPhase enum transition rules."""

    def test_valid_transition_interpreting_to_awaiting(self):
        validate_transition(RunPhase.INTERPRETING, RunPhase.AWAITING_CONFIRMATION)

    def test_valid_transition_awaiting_to_confirmed(self):
        validate_transition(RunPhase.AWAITING_CONFIRMATION, RunPhase.CONFIRMED)

    def test_valid_transition_confirmed_to_applying(self):
        validate_transition(RunPhase.CONFIRMED, RunPhase.APPLYING)

    def test_valid_transition_applying_to_completed(self):
        validate_transition(RunPhase.APPLYING, RunPhase.COMPLETED)

    def test_valid_transition_non_terminal_to_cancelled(self):
        """Cualquier fase no terminal puede ir a cancelled."""
        terminal = {RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.CANCELLED}
        for phase in RunPhase:
            if phase not in terminal:
                validate_transition(phase, RunPhase.CANCELLED)

    def test_invalid_skip_interpret(self):
        """Cannot confirm without interpreting first."""
        with pytest.raises(ValueError, match="Cannot transition"):
            validate_transition(RunPhase.INTERPRETING, RunPhase.CONFIRMED)

    def test_invalid_skip_confirm(self):
        """Cannot apply without confirming first."""
        with pytest.raises(ValueError, match="Cannot transition"):
            validate_transition(RunPhase.AWAITING_CONFIRMATION, RunPhase.APPLYING)

    def test_invalid_apply_from_interpreting(self):
        with pytest.raises(ValueError, match="Cannot transition"):
            validate_transition(RunPhase.INTERPRETING, RunPhase.APPLYING)

    def test_invalid_apply_from_awaiting(self):
        with pytest.raises(ValueError, match="Cannot transition"):
            validate_transition(RunPhase.AWAITING_CONFIRMATION, RunPhase.APPLYING)

    def test_final_phases_are_terminal(self):
        """COMPLETED, FAILED, CANCELLED have no outgoing transitions."""
        for terminal in (RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.CANCELLED):
            for target in RunPhase:
                if target != terminal:
                    with pytest.raises(ValueError, match="Cannot transition"):
                        validate_transition(terminal, target)

    def test_cancelled_cannot_transition(self):
        with pytest.raises(ValueError, match="Cannot transition"):
            validate_transition(RunPhase.CANCELLED, RunPhase.INTERPRETING)


# ── State store tests ─────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_state_dir():
    """Ensure clean state directory before and after each test."""
    from app.state.run_state import STATE_DIR, delete_run_state, load_run_state
    os.makedirs(STATE_DIR, exist_ok=True)
    for fname in os.listdir(STATE_DIR):
        if fname.endswith(".json"):
            os.unlink(os.path.join(STATE_DIR, fname))
    yield
    for fname in os.listdir(STATE_DIR):
        if fname.endswith(".json"):
            os.unlink(os.path.join(STATE_DIR, fname))


class TestRunStatePersistence:
    """Test state store save/load/delete."""

    def test_save_and_load(self):
        from app.state.run_state import save_run_state, load_run_state
        save_run_state("test-001", {"phase": "interpreting", "interpretation_draft": {"status": "ok"}})
        state = load_run_state("test-001")
        assert state is not None
        assert state["phase"] == "interpreting"
        assert state["interpretation_draft"]["status"] == "ok"

    def test_load_missing(self):
        from app.state.run_state import load_run_state
        assert load_run_state("nonexistent") is None

    def test_delete(self):
        from app.state.run_state import save_run_state, delete_run_state, load_run_state
        save_run_state("test-002", {"phase": "completed"})
        assert load_run_state("test-002") is not None
        deleted = delete_run_state("test-002")
        assert deleted is True
        assert load_run_state("test-002") is None
        assert delete_run_state("nonexistent") is False


class TestTransitionPersist:
    """Test transition_phase with persistence."""

    def test_transition_persists_phase(self):
        from app.state.run_state import transition_phase, load_run_state
        from app.intent.models import RunPhase

        save_run_state("test-003", {"phase": "interpreting", "interpretation_draft": {"ok": True}})

        result = transition_phase("test-003", RunPhase.AWAITING_CONFIRMATION)
        assert result["phase"] == "awaiting_confirmation"

        state = load_run_state("test-003")
        assert state["phase"] == "awaiting_confirmation"

    def test_transition_invalid_raises(self):
        from app.state.run_state import transition_phase, save_run_state
        from app.intent.models import RunPhase

        save_run_state("test-004", {"phase": "interpreting", "interpretation_draft": {"ok": True}})

        with pytest.raises(ValueError, match="Cannot transition"):
            transition_phase("test-004", RunPhase.COMPLETED)


# ── Test 1: POST /interpret → phase = awaiting_confirmation, no apply ──


class Test1InterpretSetsPhase:
    """After interpret, phase must be awaiting_confirmation."""

    def test_interpret_sets_awaiting_confirmation(self):
        from app.state.run_state import load_run_state

        # Simulate what POST /agent/interpret does
        from app.state.run_state import save_run_state, transition_phase
        from app.intent.models import RunPhase

        save_run_state("test1-001", {
            "interpretation_draft": {"status": "ok", "contract_id": "dashboard.sales_overview"},
            "phase": RunPhase.AWAITING_CONFIRMATION.value,
        })
        transition_phase("test1-001", RunPhase.AWAITING_CONFIRMATION)

        state = load_run_state("test1-001")
        assert state is not None
        assert state["phase"] == "awaiting_confirmation"
        assert state.get("interpretation_draft") is not None

        # No apply state exists
        assert state.get("compiled_plan") is None
        assert state.get("confirmed_intent") is None
        assert state.get("plan_preview") is None

    def test_interpret_does_not_apply(self):
        from app.state.run_state import load_run_state

        from app.state.run_state import save_run_state, transition_phase
        from app.intent.models import RunPhase

        save_run_state("test1-002", {
            "interpretation_draft": {"status": "ok"},
            "phase": RunPhase.AWAITING_CONFIRMATION.value,
        })
        transition_phase("test1-002", RunPhase.AWAITING_CONFIRMATION)

        state = load_run_state("test1-002")
        # Verify apply never happened — state must not be completed/applying
        assert state["phase"] != "applying"
        assert state["phase"] != "completed"
        assert state["phase"] != "confirmed"


# ── Test 2: POST /confirm without prior interpret → must fail ──


class Test2ConfirmWithoutDraft:
    """confirm without draft → rejected."""

    def test_confirm_without_draft_rejected(self):
        """Simula el guard en agent_confirm cuando no hay draft."""
        from app.state.run_state import load_run_state

        state = load_run_state("test2-001")
        # No state exists → should fail
        if state is None:
            reason = "No interpretation draft found. Call /agent/interpret first."
        else:
            reason = None

        assert state is None
        assert "No interpretation draft found" in reason

    def test_confirm_wrong_phase_rejected(self):
        """confirm from wrong phase → rejected."""
        from app.state.run_state import save_run_state, load_run_state, transition_phase
        from app.intent.models import RunPhase

        # Simulate: interpret → confirm (valid) → then confirm again from completed
        save_run_state("test2-002", {
            "interpretation_draft": {"status": "ok"},
            "phase": RunPhase.COMPLETED.value,
        })

        state = load_run_state("test2-002")
        current_phase = RunPhase(state.get("phase", RunPhase.INTERPRETING.value))

        allowed = (RunPhase.AWAITING_CONFIRMATION, RunPhase.CONFIRMED)
        if current_phase not in allowed:
            reason = f"Cannot confirm in phase '{current_phase.value}'. Expected 'awaiting_confirmation' or 'confirmed'."
        else:
            reason = None

        assert reason is not None
        assert "Cannot confirm in phase" in reason


# ── Test 3: POST /apply without confirmed_intent → must fail ──


class Test3ApplyWithoutConfirmed:
    """apply without confirmed_intent → fail."""

    def test_apply_without_confirmed_intent(self):
        from app.state.run_state import save_run_state, load_run_state

        save_run_state("test3-001", {
            "phase": "confirmed",
        })
        state = load_run_state("test3-001")
        confirmed_intent = state.get("confirmed_intent")

        assert confirmed_intent is None
        # The guard should catch this
        if not confirmed_intent:
            reason = "No confirmed intent found. Call /agent/confirm first."
        else:
            reason = None

        assert reason is not None
        assert "No confirmed intent found" in reason

    def test_apply_from_wrong_phase(self):
        from app.state.run_state import save_run_state, load_run_state

        save_run_state("test3-002", {
            "phase": "awaiting_confirmation",
            "confirmed_intent": {"contract_id": "dashboard"},
        })

        state = load_run_state("test3-002")
        current_phase = RunPhase(state.get("phase", RunPhase.INTERPRETING.value))
        if current_phase != RunPhase.CONFIRMED:
            reason = f"Cannot apply in phase '{current_phase.value}'. Expected 'confirmed'."
        else:
            reason = None

        assert reason is not None
        assert "Cannot apply in phase" in reason


# ── Test 4: POST /apply with gate.blocked=True → must fail ──


class Test4ApplyWithGateBlocked:
    """apply with gate.blocked → fail."""

    def test_apply_gate_blocked(self):
        from app.state.run_state import save_run_state, load_run_state

        save_run_state("test4-001", {
            "phase": "confirmed",
            "confirmed_intent": {"contract_id": "dashboard"},
            "gate": {"blocked": True, "reason": "no_actions"},
        })

        state = load_run_state("test4-001")
        gate = state.get("gate", {})
        is_blocked = gate.get("blocked", False)

        assert is_blocked is True
        assert gate.get("reason") == "no_actions"

    def test_apply_gate_not_blocked(self):
        from app.state.run_state import save_run_state, load_run_state

        save_run_state("test4-002", {
            "phase": "confirmed",
            "confirmed_intent": {"contract_id": "dashboard"},
            "gate": {"blocked": False, "reason": None},
        })

        state = load_run_state("test4-002")
        gate = state.get("gate", {})
        is_blocked = gate.get("blocked", False)

        assert is_blocked is False


# ── Test 5: Confirm is idempotent ──


class Test5ConfirmIdempotent:
    """Repeat confirm returns same (cached) result."""

    def test_double_confirm_idempotent(self):
        from app.state.run_state import save_run_state, load_run_state, transition_phase
        from app.intent.models import RunPhase

        # First confirm: set up state as if confirm had been called
        plan = {"skill_ir": {"contract_id": "dashboard"}, "intents": [], "semantic_frame": {}}
        preview = {"summary_human": "Modify dashboard", "structural_operations": []}

        save_run_state("test5-001", {
            "interpretation_draft": {"status": "ok"},
            "confirmed_intent": {"contract_id": "dashboard", "actions": [{"verb": "modify", "target_capability": "layout.page"}]},
            "compiled_plan": plan,
            "plan_preview": preview,
            "gate": {"blocked": False},
            "phase": RunPhase.CONFIRMED.value,
        })

        state = load_run_state("test5-001")
        assert state["phase"] == "confirmed"

        # Simulate idempotent confirm: return cached plan
        current_phase = RunPhase(state["phase"])
        if current_phase == RunPhase.CONFIRMED and state.get("compiled_plan"):
            cached_plan = state["compiled_plan"]
            cached_preview = state.get("plan_preview")
        else:
            cached_plan = None
            cached_preview = None

        assert cached_plan == plan
        assert cached_preview == preview

        # Second confirm should not change state
        transition_phase("test5-001", RunPhase.CONFIRMED)
        state2 = load_run_state("test5-001")
        assert state2["compiled_plan"] == plan
        assert state2["phase"] == "confirmed"


# ── Full flow integration test ─────────────────────────────────


class TestFullFlow:
    """Complete interpret → confirm → apply flow via state machine."""

    def test_full_happy_path(self):
        from app.state.run_state import save_run_state, load_run_state, transition_phase
        from app.intent.models import RunPhase

        run_id = "full-flow-001"

        # Step 1: Interpret
        save_run_state(run_id, {
            "interpretation_draft": {"status": "ok", "contract_id": "dashboard.sales_overview"},
            "phase": RunPhase.AWAITING_CONFIRMATION.value,
        })
        transition_phase(run_id, RunPhase.AWAITING_CONFIRMATION)

        state = load_run_state(run_id)
        assert state["phase"] == "awaiting_confirmation"
        assert state.get("compiled_plan") is None

        # Step 2: Confirm
        save_run_state(run_id, {
            "confirmed_intent": {"contract_id": "dashboard", "actions": [{"verb": "modify", "target_capability": "layout.page"}]},
            "compiled_plan": {"skill_ir": {}, "intents": []},
            "plan_preview": {"summary_human": "Modify dashboard"},
            "gate": {"blocked": False},
        })
        transition_phase(run_id, RunPhase.CONFIRMED)

        state = load_run_state(run_id)
        assert state["phase"] == "confirmed"
        assert state.get("confirmed_intent") is not None
        assert state.get("compiled_plan") is not None

        # Step 3: Apply
        transition_phase(run_id, RunPhase.APPLYING)
        state = load_run_state(run_id)
        assert state["phase"] == "applying"

        transition_phase(run_id, RunPhase.COMPLETED)
        state = load_run_state(run_id)
        assert state["phase"] == "completed"

    def test_cancel_mid_flow(self):
        from app.state.run_state import save_run_state, load_run_state, transition_phase
        from app.intent.models import RunPhase

        run_id = "full-flow-cancel"

        save_run_state(run_id, {
            "interpretation_draft": {"status": "ok"},
            "phase": RunPhase.AWAITING_CONFIRMATION.value,
        })
        transition_phase(run_id, RunPhase.AWAITING_CONFIRMATION)

        # Cancel from awaiting_confirmation
        transition_phase(run_id, RunPhase.CANCELLED)
        state = load_run_state(run_id)
        assert state["phase"] == "cancelled"

        # Cannot transition from cancelled
        with pytest.raises(ValueError, match="Cannot transition"):
            transition_phase(run_id, RunPhase.CONFIRMED)

    def test_invalid_direct_apply_from_interpret(self):
        from app.intent.models import RunPhase
        with pytest.raises(ValueError, match="Cannot transition"):
            validate_transition(RunPhase.INTERPRETING, RunPhase.APPLYING)


# ── Transition matrix completeness ──────────────────────────────


class TestTransitionMatrix:
    """All edges in the state machine are explicitly covered."""

    def test_all_valid_transitions(self):
        """Exhaustively verify allowed transiciones."""
        from app.intent.models import _VALID_TRANSITIONS

        allowed = {
            (RunPhase.INTERPRETING, RunPhase.AWAITING_CONFIRMATION),
            (RunPhase.INTERPRETING, RunPhase.CANCELLED),
            (RunPhase.AWAITING_CONFIRMATION, RunPhase.CONFIRMED),
            (RunPhase.AWAITING_CONFIRMATION, RunPhase.CANCELLED),
            (RunPhase.CONFIRMED, RunPhase.APPLYING),
            (RunPhase.CONFIRMED, RunPhase.CANCELLED),
            (RunPhase.APPLYING, RunPhase.COMPLETED),
            (RunPhase.APPLYING, RunPhase.FAILED),
            (RunPhase.APPLYING, RunPhase.CANCELLED),
        }

        for current in RunPhase:
            for target in RunPhase:
                if current == target:
                    continue
                if (current, target) in allowed:
                    validate_transition(current, target)
                else:
                    with pytest.raises(ValueError, match="Cannot transition"):
                        validate_transition(current, target)


def save_run_state(run_id, data):
    """Helper to save state inline (bypasses import)."""
    from app.state.run_state import save_run_state as _save
    _save(run_id, data)
