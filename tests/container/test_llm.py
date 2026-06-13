"""Fase 5.1 — LLM-dependent tests.

Exercises interpret() with a REAL LLM via LLMClient (no HTTP API).
Requires: vllm container running.

Mark with @pytest.mark.llm (auto-skipped if vllm is down).

Note: proposed_actions es list[dict], los tests acceden con []
no con .attr.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.intent.interpreter import interpret as llm_interpret


pytestmark = pytest.mark.llm


class TestLLMInterpret:
    def test_remove_kpi(self):
        draft = llm_interpret("remove the KPI row")
        assert draft.status == "ok"
        actions = draft.proposed_actions
        assert len(actions) == 1
        assert actions[0]["verb"] == "remove"
        assert actions[0]["target_capability"] == "presentation.kpi_row"

    def test_add_timeseries(self):
        draft = llm_interpret("add a line chart")
        assert draft.status == "ok"
        actions = draft.proposed_actions
        assert len(actions) >= 1
        assert actions[0]["verb"] in ("create", "add")
        assert "timeseries" in actions[0].get("target_capability", "")

    def test_update_metrics(self):
        draft = llm_interpret("update KPI metrics to revenue and growth")
        assert draft.status == "ok"
        actions = draft.proposed_actions
        assert len(actions) == 1
        assert actions[0]["verb"] == "modify"
        assert actions[0]["target_capability"] == "presentation.kpi_row"
        params = draft.params_proposed
        assert "revenue" in str(params).lower() or "revenue" in str(params.get("metrics", []))

    def test_update_dashboard(self):
        draft = llm_interpret("update dashboard")
        # "update dashboard" is ambiguous; real LLM may ask for clarification
        assert draft.status in ("ok", "needs_clarification")
        if draft.status == "ok":
            targets = {a["target_capability"] for a in draft.proposed_actions}
            assert "layout.page" in targets

    def test_invalid_needs_clarification(self):
        draft = llm_interpret("I like the dashboard")
        assert draft.status == "needs_clarification"

    def test_unsupported_rejected(self):
        draft = llm_interpret("fix the database schema")
        assert draft.status in ("unsupported", "needs_clarification")

    def test_semantic_confidence(self):
        draft = llm_interpret("remove the trend chart")
        assert draft.status == "ok"
        for action in draft.proposed_actions:
            assert action.get("confidence", 0) > 0.5
