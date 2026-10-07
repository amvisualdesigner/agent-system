"""Test IntentInterpreter — contract selection + validation logic.

These tests exercise the deterministic parts of the interpreter
(contract selection, action detection, validation) without calling LLM.
"""

from __future__ import annotations

import pytest

from app.intent.interpreter import (
    _select_contract,
    _has_action_verb,
    _build_worktree_caps,
    _score_contract,
    _validate_params,
)


class TestContractSelection:
    def test_select_dashboard_from_kpi(self):
        assert _select_contract("Remove the KPI row") == "dashboard.sales_overview"

    def test_select_dashboard_from_metrics(self):
        assert _select_contract("Update metrics to revenue and growth") == "dashboard.sales_overview"

    def test_select_dashboard_from_line_chart(self):
        assert _select_contract("Remove the line chart") == "dashboard.sales_overview"

    def test_select_table_from_columns(self):
        assert _select_contract("Modify the analytics table columns") == "analytics.table"

    def test_select_table_from_data_table(self):
        assert _select_contract("Show the data table") == "analytics.table"

    def test_select_table_from_table_keyword(self):
        assert _select_contract("Modify the data table columns") == "analytics.table"

    def test_select_filter_panel(self):
        assert _select_contract("Add a filter for date range") == "analytics.filter"

    def test_select_filter_panel_spanish_filtro(self):
        assert _select_contract("Crea un filtro con Región y Producto") == "analytics.filter"

    def test_select_filter_panel_spanish_filtrar(self):
        assert _select_contract("Quiero filtrar los resultados por fecha") == "analytics.filter"

    def test_select_filter_panel_spanish_filtros(self):
        assert _select_contract("Añade filtros para el dashboard de ventas") == "analytics.filter"

    def test_select_filter_panel_spanish_panel_de_filtros(self):
        assert _select_contract("Pon un panel de filtros en la página") == "analytics.filter"

    def test_select_filter_panel_spanish_facetar(self):
        assert _select_contract("Facetar las ventas por región") == "analytics.filter"

    def test_select_filter_panel_english_keywords_still_work(self):
        assert _select_contract("Add a filter for date range") == "analytics.filter"
        assert _select_contract("Refine by region with a facet") == "analytics.filter"
        assert _select_contract("Filter the dashboard") == "analytics.filter"
        assert _select_contract("Add a filter panel for region and channel") == "analytics.filter"

    def test_select_filter_main_case_not_dashboard(self):
        # "Add a filter by size in sales dashboard": la intencion de filtro
        # NUNCA se va al dashboard solo por mencionarlo (Fase Contract Selection).
        assert _select_contract("Add a filter by size in sales dashboard") == "analytics.filter"
        assert _select_contract("Filter the sales dashboard by size") == "analytics.filter"

    def test_select_filter_with_preposition_target(self):
        assert _select_contract("Add a filter in sales dashboard") == "analytics.filter"
        assert _select_contract("Add a filter to the dashboard by region") == "analytics.filter"
        assert _select_contract("Change the filter on the dashboard") == "analytics.filter"
        assert _select_contract("Remove the filter from the sales dashboard") == "analytics.filter"

    def test_select_bar_chart(self):
        assert _select_contract("Create a bar chart of revenue by quarter") == "analytics.chart_bar"

    def test_select_unsupported(self):
        assert _select_contract("What is the meaning of life?") is None

    def test_empty_message(self):
        assert _select_contract("") is None


class TestActionDetection:
    def test_detect_remove(self):
        assert _has_action_verb("Remove the KPI row") is True

    def test_detect_modify(self):
        assert _has_action_verb("Update metrics to revenue") is True

    def test_detect_create(self):
        assert _has_action_verb("Add a bar chart") is True

    def test_detect_delete_synonym(self):
        assert _has_action_verb("Delete the timeseries") is True

    def test_no_action(self):
        assert _has_action_verb("The dashboard looks nice") is False

    def test_detect_filter_create_verb(self):
        # "filter"/"refine"/"filtro" habilitan create: el caso main no cae en
        # clarification_needed por falta de verbo de accion.
        assert _has_action_verb("Filter the sales dashboard by size") is True
        assert _has_action_verb("Refine by region") is True
        assert _has_action_verb("Filtra el dashboard por tamaño") is True

    def test_empty_message_no_action(self):
        assert _has_action_verb("") is False


class TestScoreContract:
    def test_score_dashboard_high(self):
        score = _score_contract("dashboard.sales_overview", "remove the kpi row from the sales dashboard")
        assert score > 0.1

    def test_score_table_high(self):
        score = _score_contract("analytics.table", "show revenue and growth columns in the table")
        assert score > 0.1

    def test_score_no_match(self):
        score = _score_contract("analytics.table", "remove the kpi row")
        assert score == 0.0


class TestValidateParams:
    def test_filter_params_any_string_list_is_valid(self):
        # analytics.filter: filters=array<string> SIN enum => ["size"] es valido.
        assert _validate_params({"filters": ["size"]}, "analytics.filter") == []

    def test_filter_params_non_string_rejected(self):
        errors = _validate_params({"filters": [123]}, "analytics.filter")
        assert len(errors) == 1
        assert "filters" in errors[0]

    def test_dashboard_enum_violation_rejected(self):
        errors = _validate_params(
            {"metrics": ["revenue", "size"]}, "dashboard.sales_overview",
        )
        assert any("metrics" in e for e in errors)

    def test_dashboard_timeseries_enum_violation_rejected(self):
        errors = _validate_params(
            {"timeseries_metric": "bogus"}, "dashboard.sales_overview",
        )
        assert any("timeseries_metric" in e for e in errors)

    def test_valid_dashboard_params_pass(self):
        assert _validate_params(
            {"metrics": ["revenue"], "timeseries_metric": "growth"},
            "dashboard.sales_overview",
        ) == []

    def test_unknown_params_are_not_blocking(self):
        assert _validate_params({"size": "medium"}, "analytics.filter") == []

    def test_non_dict_params_rejected(self):
        errors = _validate_params([1, 2], "analytics.filter")
        assert errors

    def test_unknown_contract_no_errors(self):
        assert _validate_params({"metrics": ["size"]}, "no.such_contract") == []


class TestWorktreeCaps:
    def test_build_with_snapshot(self):
        entry = {
            "capabilities": [
                {"id": "presentation.kpi_row", "label": "KPI row"},
                {"id": "presentation.timeseries", "label": "Trend chart"},
            ]
        }
        snapshot = {"presentation.kpi_row": ["src/KpiRow.tsx"]}
        caps = _build_worktree_caps(entry, snapshot)
        assert len(caps) == 2
        assert caps[0]["id"] == "presentation.kpi_row"
        assert caps[0]["present"] is True
        assert caps[0]["paths"] == ["src/KpiRow.tsx"]
        assert caps[1]["present"] is False

    def test_build_without_snapshot(self):
        entry = {
            "capabilities": [
                {"id": "presentation.kpi_row", "label": "KPI row"},
            ]
        }
        caps = _build_worktree_caps(entry, None)
        assert len(caps) == 1
        assert caps[0]["present"] is False
        assert caps[0]["paths"] == []
