"""Fase 5B — orchestrator mirrors the backend lifecycle.

The backend `RunPhase` is the only lifecycle authority. These tests lock the
orchestrator's mirror of it: phase vocabulary, the G6 clarification guard, the
structured disambiguation passthrough, and retry/cancel preconditions.
"""

import pathlib
import re

import pytest

ORCH = pathlib.Path(__file__).resolve().parents[2] / "orchestrator"

NODES = (
    "graph.py",
    "main.py",
    "state.py",
    "nodes/interpret.py",
    "nodes/confirm.py",
    "nodes/validate_plan.py",
    "nodes/call_apply.py",
    "nodes/return_result.py",
    "models.py",
)

BACKEND_PHASES = {
    "interpreting", "awaiting_confirmation", "confirmed",
    "applying", "completed", "failed", "cancelled",
}


def _read(name: str) -> str:
    return (ORCH / name).read_text(encoding="utf-8")


class TestPhaseVocabulary:
    def test_no_legacy_phase_names(self):
        for name in NODES:
            text = _read(name)
            assert "awaiting_apply" not in text, name
            assert "awaiting_apply" not in text, name

    def test_terminal_ok_flag_removed(self):
        text = _read("nodes/return_result.py")
        assert "terminal_ok" not in text

    def test_conflict_is_not_terminal(self):
        text = _read("nodes/return_result.py")
        block = text.split("phase_label = ")[1]
        assert '"conflict"' in block
        # conflict → confirmed, never → completed/failed
        assert re.search(
            r'elif result\.status == "conflict":\s*\n\s*phase_label = "confirmed"', text
        )

    def test_failure_statuses_settle_in_failed(self):
        text = _read("nodes/return_result.py")
        assert "verify_failed" in text
        assert "rejected" in text
        line = [l for l in text.splitlines() if 'phase_label = "failed"' in l]
        assert line, "no failed settlement"

    def test_every_emitted_phase_is_backend_known(self):
        for name in NODES:
            for phase in re.findall(r'phase_label = "([a-z_]+)"', _read(name)):
                assert phase in BACKEND_PHASES, f"{name}: {phase}"


class TestClarificationGuard:
    GUARD = "if interp_status == \"clarification_needed\":"

    def _guard_block(self) -> str:
        text = _read("nodes/confirm.py")
        start = text.index(self.GUARD)
        return text[start:start + 3000]

    def test_guard_present(self):
        assert self.GUARD in _read("nodes/confirm.py")

    def test_guard_never_returns_failed(self):
        """A blocked confirmation must stay parked in awaiting_confirmation."""
        block = self._guard_block()
        assert '"failed"' not in block
        assert '_error_state' not in block

    def test_guard_accepts_structured_answers(self):
        block = self._guard_block()
        assert "page_context_choice" in block
        assert "alternative_index" in block
        assert "selected_alternative" in block
        assert "instance_hint" in block

    def test_guard_has_no_dead_end_for_generic_clarification(self):
        block = self._guard_block()
        assert "rephrase your request first" in block

    def test_alternatives_passthrough_to_backend(self):
        text = _read("nodes/confirm.py")
        assert 'confirm_payload["alternative_index"]' in text
        assert 'confirm_payload["selected_alternative"]' in text


class TestConfirmIntentCarriesDecisions:
    def test_main_forwards_alternative_selection(self):
        text = _read("main.py")
        block = text.split("confirmed_intent = {")[1].split('start_node="confirm"')[0]
        assert '"alternative_index"' in block
        assert '"selected_alternative"' in block

    def test_gate_is_top_level_in_payload(self):
        text = _read("main.py")
        assert 'gate = snapshot.get("gate")' in text


class TestRecoveryEndpoints:
    def test_retry_requires_confirmed_phase(self):
        text = _read("main.py")
        block = text.split("def retry_run")[1].split("@app.post")[0]
        assert 'phase != "confirmed"' in block
        assert "status_code=409" in block

    def test_retry_rejects_non_retryable_conflict(self):
        text = _read("main.py")
        block = text.split("def retry_run")[1].split("@app.post")[0]
        assert "not retryable" in block

    def test_retry_marks_same_plan(self):
        text = _read("main.py")
        block = text.split("def retry_run")[1].split("@app.post")[0]
        assert 'state["is_retry"] = True' in block

    def test_cancel_phase_whitelist(self):
        text = _read("main.py")
        block = text.split("def cancel_run")[1].split("@app.get")[0]
        assert '"interpreting", "awaiting_confirmation", "confirmed"' in block
        assert '"applying"' not in block
        assert "status_code=409" in block

    def test_cancel_uses_dedicated_model(self):
        text = _read("main.py")
        block = text.split("def cancel_run")[1].split("@app.get")[0]
        assert "CancelRequest" in block
        assert "ApplyRequest" not in block

    def test_models_expose_retry_and_cancel(self):
        text = _read("models.py")
        assert "class RetryRequest" in text
        assert "class CancelRequest" in text


@pytest.mark.parametrize("name", NODES)
def test_every_node_file_parses(name):
    compile(_read(name), name, "exec")