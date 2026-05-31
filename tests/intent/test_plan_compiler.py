"""Test PlanCompiler — determinista, sin LLM, sin index."""

from __future__ import annotations

import pytest

from app.intent.models import ConfirmedIntent, IntentAction
from app.intent.plan_compiler import compile_plan


def test_compile_dashboard_remove_kpi():
    confirmed = ConfirmedIntent(
        contract_id="dashboard.sales_overview",
        contract_version=1,
        actions=[
            IntentAction(verb="remove", target_capability="presentation.kpi_row"),
        ],
        params={},
        user_message="Remove the KPI row from the sales dashboard",
        interpretation_id="test-001",
    )

    plan = compile_plan(confirmed)
    assert plan.contract_id == "dashboard.sales_overview"
    assert plan.skill_ir["contract_id"] == "dashboard.sales_overview"
    assert plan.skill_ir["version"] == 1
    assert len(plan.intents) == 1
    assert plan.intents[0]["capability"] == "presentation.kpi_row"
    assert plan.intents[0]["source"] == "confirmed_intent"


def test_compile_dashboard_modify_metrics():
    confirmed = ConfirmedIntent(
        contract_id="dashboard.sales_overview",
        contract_version=1,
        actions=[
            IntentAction(
                verb="modify",
                target_capability="presentation.kpi_row",
                params={"metrics": ["revenue", "growth"]},
            ),
        ],
        params={"metrics": ["revenue", "growth"]},
        user_message="Update KPI metrics to revenue and growth",
        interpretation_id="test-002",
    )

    plan = compile_plan(confirmed)
    assert plan.contract_id == "dashboard.sales_overview"
    assert plan.skill_ir["params"]["metrics"] == ["revenue", "growth"]
    # Contract default for timeseries_metric should be filled
    assert "timeseries_metric" in plan.skill_ir["params"]
    assert plan.skill_ir["params"]["timeseries_metric"] == "revenue"
    assert len(plan.semantic_frame["actions"]) == 1
    assert plan.semantic_frame["actions"][0]["verb"] == "modify"


def test_compile_multiple_actions():
    confirmed = ConfirmedIntent(
        contract_id="dashboard.sales_overview",
        contract_version=1,
        actions=[
            IntentAction(verb="remove", target_capability="presentation.timeseries"),
            IntentAction(verb="modify", target_capability="presentation.kpi_row", params={"metrics": ["revenue"]}),
        ],
        params={"metrics": ["revenue"]},
        user_message="Remove the chart and update KPI metrics",
        interpretation_id="test-003",
    )

    plan = compile_plan(confirmed)
    assert len(plan.intents) == 2
    assert len(plan.semantic_frame["actions"]) == 2
    assert len(plan.semantic_frame["objects"]) == 2


def test_compile_unknown_contract():
    confirmed = ConfirmedIntent(
        contract_id="nonexistent.contract",
        contract_version=1,
        actions=[IntentAction(verb="modify", target_capability="presentation.kpi_row")],
        params={},
        user_message="test",
        interpretation_id="test-004",
    )
    with pytest.raises(ValueError, match="not found"):
        compile_plan(confirmed)


def test_compile_semantic_frame_format():
    """Verify semantic_frame format is compatible with ApplyEngine."""
    confirmed = ConfirmedIntent(
        contract_id="dashboard.sales_overview",
        contract_version=1,
        actions=[
            IntentAction(verb="remove", target_capability="presentation.kpi_row"),
        ],
        params={},
        user_message="Remove KPI row",
        interpretation_id="test-005",
    )

    plan = compile_plan(confirmed)
    sf = plan.semantic_frame

    # Must have actions list
    assert "actions" in sf
    assert isinstance(sf["actions"], list)
    if sf["actions"]:
        a = sf["actions"][0]
        # Each action should have verb and object
        assert "verb" in a
        assert "object" in a or "direct_object" in a

    # Must have confidence
    assert "confidence" in sf
    assert sf["confidence"] == 1.0
