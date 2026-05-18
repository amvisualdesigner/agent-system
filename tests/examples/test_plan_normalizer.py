import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.engine.plan_normalizer import normalize_plan, COMPONENT_MAP


class TestPlanNormalizer(unittest.TestCase):

    def test_known_component_passes_through(self):
        plan = {"actions": [{"component": "KpiRow", "props": {"metrics": ["revenue"]}}]}
        registry = {"KpiRow", "Timeseries"}
        result = normalize_plan(plan, registry)
        self.assertEqual(result["actions"], plan["actions"])

    def test_revenue_chart_mapped_to_timeseries(self):
        plan = {"actions": [{"component": "RevenueChart"}]}
        registry = {"KpiRow", "Timeseries"}
        result = normalize_plan(plan, registry)
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["component"], "Timeseries")
        self.assertEqual(result["actions"][0]["props"]["metric"], "revenue")

    def test_revenue_table_mapped_to_kpi_row(self):
        plan = {"actions": [{"component": "RevenueTable", "props": {"period": "Q1"}}]}
        registry = {"KpiRow", "Timeseries"}
        result = normalize_plan(plan, registry)
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["component"], "KpiRow")
        self.assertEqual(result["actions"][0]["props"]["period"], "Q1")

    def test_unknown_component_dropped(self):
        plan = {"actions": [{"component": "FlyingWidget"}]}
        registry = {"KpiRow", "Timeseries"}
        result = normalize_plan(plan, registry)
        self.assertEqual(result["actions"], [])

    def test_mixed_known_mapped_and_dropped(self):
        plan = {
            "actions": [
                {"component": "KpiRow", "props": {"metrics": ["revenue"]}},
                {"component": "RevenueChart"},
                {"component": "BogusComponent"},
                {"component": "Timeseries", "props": {"metric": "growth"}},
            ]
        }
        registry = {"KpiRow", "Timeseries"}
        result = normalize_plan(plan, registry)
        self.assertEqual(len(result["actions"]), 3)
        self.assertEqual(result["actions"][0]["component"], "KpiRow")
        self.assertEqual(result["actions"][1]["component"], "Timeseries")
        self.assertEqual(result["actions"][1]["props"]["metric"], "revenue")
        self.assertEqual(result["actions"][2]["component"], "Timeseries")
        self.assertEqual(result["actions"][2]["props"]["metric"], "growth")

    def test_empty_actions(self):
        plan = {"actions": []}
        registry = {"KpiRow"}
        result = normalize_plan(plan, registry)
        self.assertEqual(result["actions"], [])

    def test_no_mutation_of_input(self):
        original = {"actions": [{"component": "RevenueChart"}]}
        registry = {"KpiRow", "Timeseries"}
        normalize_plan(original, registry)
        self.assertEqual(original["actions"][0]["component"], "RevenueChart")

    def test_no_actions_key(self):
        plan = {"other": "data"}
        registry = {"KpiRow"}
        result = normalize_plan(plan, registry)
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["other"], "data")

    def test_revenue_table_preserves_existing_props(self):
        plan = {"actions": [{"component": "RevenueTable", "props": {"period": "Q1"}}]}
        registry = {"KpiRow", "Timeseries"}
        result = normalize_plan(plan, registry)
        self.assertEqual(result["actions"][0]["component"], "KpiRow")
        self.assertEqual(result["actions"][0]["props"]["period"], "Q1")

    def test_deterministic(self):
        plan = {
            "actions": [
                {"component": "RevenueChart"},
                {"component": "KpiRow"},
                {"component": "RevenueTable"},
                {"component": "FakeWidget"},
            ]
        }
        registry = {"KpiRow", "Timeseries"}
        r1 = normalize_plan(plan, registry)
        r2 = normalize_plan(plan, registry)
        self.assertEqual(r1, r2)

    def test_component_map_not_empty(self):
        self.assertIn("RevenueChart", COMPONENT_MAP)
        self.assertIn("RevenueTable", COMPONENT_MAP)
        self.assertEqual(COMPONENT_MAP["RevenueChart"], "Timeseries")
        self.assertEqual(COMPONENT_MAP["RevenueTable"], "KpiRow")


if __name__ == "__main__":
    unittest.main()
