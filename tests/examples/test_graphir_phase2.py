"""Phase 2 tests: Intent Coverage Layer.

Test coverage:
  1. Intent dataclass — construction, immutability, ID generation
  2. Intent decomposition — task → list[Intent]
  3. Intent coverage validator — registry matching, LLM fallback, reports
  4. GraphIR coverage revalidation — bijection constraint
  5. Intent fidelity is real (not hardcoded)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.intent import (
    Intent,
    IntentPlan,
    IntentNode,
    make_intent_id,
    resolve_graphir_type_from_capability,
    resolve_edge_role_from_capability,
)
from app.graphir.intent_decomposition import decompose_task, _keyword_decompose
from app.graphir.intent_coverage import (
    CapabilityMatch,
    MissingIntent,
    CoverageReport,
    IntentCoverageValidator,
    IntentCoverageError,
    build_capabilities_index,
)
from app.graphir.models import (
    EdgeRole,
    GraphIRNode,
    GraphIREdge,
    GraphIRDraft,
)
from app.contracts.skill_registry import SkillContract, SKILL_CONTRACTS


# ════════════════════════════════════════════════════════════
# 1. Intent — unified model
# ════════════════════════════════════════════════════════════

class TestIntent(unittest.TestCase):

    def test_construct_minimal(self):
        intent = Intent(id="i1", capability="display.kpi_row")
        self.assertEqual(intent.id, "i1")
        self.assertEqual(intent.capability, "display.kpi_row")
        self.assertEqual(intent.params, {})
        self.assertEqual(intent.task_fragment, "")
        self.assertEqual(intent.weight, 1.0)

    def test_construct_full(self):
        intent = Intent(
            id="i_test_001",
            capability="display.timeseries",
            params={"metric": "revenue"},
            task_fragment="timeseries",
            weight=0.8,
        )
        self.assertEqual(intent.params["metric"], "revenue")
        self.assertEqual(intent.task_fragment, "timeseries")
        self.assertEqual(intent.weight, 0.8)

    def test_is_immutable(self):
        intent = Intent(id="i1", capability="display.kpi_row")
        with self.assertRaises(AttributeError):
            intent.id = "changed"  # type: ignore

    def test_make_intent_id_deterministic(self):
        id1 = make_intent_id("show kpi", "display.kpi_row")
        id2 = make_intent_id("show kpi", "display.kpi_row")
        self.assertEqual(id1, id2)

    def test_make_intent_id_different_capability(self):
        id1 = make_intent_id("show kpi", "display.kpi_row")
        id2 = make_intent_id("show kpi", "display.timeseries")
        self.assertNotEqual(id1, id2)

    def test_make_intent_id_different_fragment(self):
        id1 = make_intent_id("show kpi", "display.kpi_row")
        id2 = make_intent_id("show kpi chart", "display.kpi_row")
        self.assertNotEqual(id1, id2)

    def test_resolve_graphir_type_from_capability(self):
        self.assertEqual(
            resolve_graphir_type_from_capability("display.kpi_row"),
            "KpiRow",
        )
        self.assertEqual(
            resolve_graphir_type_from_capability("display.timeseries"),
            "Timeseries",
        )
        self.assertEqual(
            resolve_graphir_type_from_capability("display.analytics_table"),
            "AnalyticsTable",
        )
        self.assertEqual(
            resolve_graphir_type_from_capability("layout.page"),
            "Page",
        )

    def test_resolve_edge_role_from_capability(self):
        self.assertEqual(
            resolve_edge_role_from_capability("display.kpi_row"),
            "PRIMARY",
        )
        self.assertEqual(
            resolve_edge_role_from_capability("display.timeseries"),
            "SUPPORTING",
        )

    def test_resolve_unknown_capability(self):
        self.assertIsNone(resolve_graphir_type_from_capability("unknown.x"))
        self.assertIsNone(resolve_edge_role_from_capability("unknown.x"))


# ════════════════════════════════════════════════════════════
# 2. Intent decomposition
# ════════════════════════════════════════════════════════════

class TestIntentDecomposition(unittest.TestCase):

    def test_decompose_empty_task(self):
        self.assertEqual(decompose_task(""), [])
        self.assertEqual(decompose_task("   "), [])

    def test_decompose_kpi_keyword(self):
        intents = decompose_task("show revenue kpi")
        caps = [i.capability for i in intents]
        self.assertIn("display.kpi_row", caps)

    def test_decompose_timeseries_keyword(self):
        intents = decompose_task("revenue over time")
        caps = [i.capability for i in intents]
        self.assertIn("display.timeseries", caps)

    def test_decompose_table_keyword(self):
        intents = decompose_task("sales data table")
        caps = [i.capability for i in intents]
        self.assertIn("display.analytics_table", caps)

    def test_decompose_multi_intent(self):
        intents = decompose_task("dashboard with kpi metrics and timeseries chart")
        caps = [i.capability for i in intents]
        self.assertIn("display.kpi_row", caps)
        self.assertIn("display.timeseries", caps)

    def test_decompose_no_duplicates(self):
        intents = decompose_task("kpi metrics and more kpi")
        caps = [i.capability for i in intents]
        self.assertEqual(caps, ["display.kpi_row"])  # no duplicate

    def test_decompose_ids_are_deterministic(self):
        t1 = decompose_task("kpi and timeseries")
        t2 = decompose_task("kpi and timeseries")
        ids1 = [(i.id, i.capability) for i in t1]
        ids2 = [(i.id, i.capability) for i in t2]
        self.assertEqual(ids1, ids2)


# ════════════════════════════════════════════════════════════
# 3. Intent Coverage Validator
# ════════════════════════════════════════════════════════════

class TestCoverageValidator(unittest.TestCase):

    def _get_contracts(self):
        return [
            SKILL_CONTRACTS[("dashboard.sales_overview", 1)],
            SKILL_CONTRACTS[("analytics.table", 1)],
        ]

    def _make_intent(self, capability: str, **params) -> Intent:
        return Intent(
            id=make_intent_id("test", capability),
            capability=capability,
            params=params,
            task_fragment="test",
        )

    def test_coverage_full_single_contract(self):
        intents = [
            self._make_intent("display.kpi_row"),
            self._make_intent("display.timeseries"),
        ]
        report = IntentCoverageValidator.check_coverage(intents, [SKILL_CONTRACTS[("dashboard.sales_overview", 1)]])
        self.assertEqual(report.coverage, 1.0)
        self.assertEqual(report.covered_intents, 2)
        self.assertEqual(len(report.missing), 0)

    def test_coverage_partial(self):
        intents = [
            self._make_intent("display.kpi_row"),
            self._make_intent("data.export"),  # not in any contract
        ]
        report = IntentCoverageValidator.check_coverage(intents, self._get_contracts())
        self.assertLess(report.coverage, 1.0)
        self.assertEqual(report.covered_intents, 1)
        self.assertEqual(len(report.missing), 1)
        self.assertEqual(report.missing[0].capability, "data.export")
        self.assertEqual(report.missing[0].reason, "no_contract")

    def test_coverage_multi_contract(self):
        intents = [
            self._make_intent("display.kpi_row"),
            self._make_intent("display.timeseries"),
            self._make_intent("display.analytics_table"),
        ]
        report = IntentCoverageValidator.check_coverage(intents, self._get_contracts())
        self.assertEqual(report.coverage, 1.0)
        self.assertEqual(report.contract_count, 2)
        self.assertEqual(len(report.missing), 0)

    def test_coverage_no_match(self):
        intents = [
            self._make_intent("data.export"),
            self._make_intent("interaction.search"),
        ]
        report = IntentCoverageValidator.check_coverage(intents, self._get_contracts())
        self.assertEqual(report.coverage, 0.0)
        self.assertEqual(len(report.missing), 2)

    def test_coverage_empty_intents(self):
        report = IntentCoverageValidator.check_coverage([], [])
        self.assertEqual(report.coverage, 1.0)
        self.assertEqual(report.total_intents, 0)

    def test_coverage_llm_fallback(self):
        intents = [
            self._make_intent("data.export"),
        ]
        def llm_fallback(intent):
            if intent.capability == "data.export":
                return CapabilityMatch(
                    intent_id=intent.id,
                    capability="data.export",
                    contract_id=None,
                    slot_type=None,
                    source="llm",
                    confidence=0.7,
                )
            return None
        report = IntentCoverageValidator.check_coverage(intents, self._get_contracts(), llm_synthesize=llm_fallback)
        self.assertEqual(report.coverage, 1.0)
        self.assertTrue(report.fallback_used)
        self.assertEqual(len(report.missing), 0)

    def test_build_capabilities_index(self):
        contracts = self._get_contracts()
        index = build_capabilities_index(contracts)
        self.assertIn("display.kpi_row", index)
        self.assertIn("display.timeseries", index)
        self.assertIn("display.analytics_table", index)
        # Each capability points to (contract_id, slot_type)
        kpi_matches = index["display.kpi_row"]
        self.assertEqual(kpi_matches[0][0], "dashboard.sales_overview")
        self.assertEqual(kpi_matches[0][1], "KpiRow")

    def test_coverage_report_has_gates(self):
        intents = [self._make_intent("display.kpi_row")]
        report = IntentCoverageValidator.check_coverage(intents, self._get_contracts())
        self.assertIn("decomposition", report.gates_passed)
        self.assertIn("coverage", report.gates_passed)
        self.assertIn("build", report.gates_passed)
        self.assertIn("revalidation", report.gates_passed)
        self.assertTrue(report.gates_passed["coverage"])

    def test_coverage_report_gate_fails_on_partial(self):
        intents = [self._make_intent("data.export")]
        report = IntentCoverageValidator.check_coverage(intents, self._get_contracts())
        self.assertFalse(report.gates_passed["coverage"])


# ════════════════════════════════════════════════════════════
# 4. GraphIR Coverage Revalidation
# ════════════════════════════════════════════════════════════

class TestCoverageRevalidation(unittest.TestCase):

    def _make_graph_with_intents(self, intent_ids: list[str]) -> GraphIRDraft:
        draft = GraphIRDraft()
        for i, iid in enumerate(intent_ids):
            nid = f"node_{i}"
            draft.add_node(GraphIRNode(
                id=nid,
                type="KpiRow",
                data={},
                metadata={"intent_id": iid, "intent_capability": "display.kpi_row"},
            ))
            if i == 0:
                draft.add_node(GraphIRNode(
                    id="Page", type="Page", data={},
                    metadata={"intent_id": "page", "intent_capability": "layout.page"},
                ))
                draft.add_edge(GraphIREdge(source="Page", target=nid, role=EdgeRole.PRIMARY))
            else:
                draft.add_edge(GraphIREdge(source="Page", target=nid, role=EdgeRole.PRIMARY))
        return draft

    def _make_coverage_report(self, intents: list[Intent]) -> CoverageReport:
        return CoverageReport(
            coverage=1.0,
            total_intents=len(intents),
            covered_intents=len(intents),
            matched=[
                CapabilityMatch(
                    intent_id=i.id, capability=i.capability,
                    contract_id="test", slot_type="Test",
                    source="registry", confidence=1.0,
                ) for i in intents
            ],
            missing=[],
        )

    def test_revalidation_passes(self):
        intents = [
            Intent(id="i1", capability="display.kpi_row"),
            Intent(id="i2", capability="display.timeseries"),
        ]
        draft = self._make_graph_with_intents(["i1", "i2"])
        graph = draft.freeze()
        report = self._make_coverage_report(intents)
        result = IntentCoverageValidator.revalidate(graph, report, intents)
        self.assertEqual(len(result.uncovered_intents), 0)
        self.assertTrue(result.gates_passed["revalidation"])

    def test_revalidation_fails_on_missing_node(self):
        intents = [
            Intent(id="i1", capability="display.kpi_row"),
            Intent(id="i2", capability="display.timeseries"),
        ]
        draft = self._make_graph_with_intents(["i1"])  # missing i2
        graph = draft.freeze()
        report = self._make_coverage_report(intents)
        with self.assertRaises(IntentCoverageError) as ctx:
            IntentCoverageValidator.revalidate(graph, report, intents)
        self.assertIn("i2", str(ctx.exception))

    def test_revalidation_builds_intent_to_nodes(self):
        intents = [Intent(id="i1", capability="display.kpi_row")]
        draft = self._make_graph_with_intents(["i1"])
        graph = draft.freeze()
        report = self._make_coverage_report(intents)
        result = IntentCoverageValidator.revalidate(graph, report, intents)
        self.assertIn("i1", result.intent_to_nodes)
        self.assertEqual(len(result.intent_to_nodes["i1"]), 1)


# ════════════════════════════════════════════════════════════
# 5. IntentPlan with Intent
# ════════════════════════════════════════════════════════════

class TestIntentPlanWithIntent(unittest.TestCase):

    def test_intent_plan_holds_intents(self):
        intent = Intent(id="i1", capability="display.kpi_row", params={"metrics": ["revenue"]})
        plan = IntentPlan(intents=[intent], params={"metrics": ["revenue"]})
        self.assertEqual(len(plan.intents), 1)
        self.assertEqual(plan.intents[0].capability, "display.kpi_row")

    def test_intent_plan_validate_passes(self):
        intent = Intent(id="i1", capability="layout.page")
        plan = IntentPlan(intents=[intent])
        IntentPlan.validate(plan)

    def test_intent_plan_validate_fails_empty(self):
        plan = IntentPlan(intents=[])
        with self.assertRaises(ValueError):
            IntentPlan.validate(plan)


# ════════════════════════════════════════════════════════════
# 6. Edge case: empty and invalid inputs
# ════════════════════════════════════════════════════════════

class TestCoverageEdgeCases(unittest.TestCase):

    def test_coverage_with_no_contracts(self):
        intent = Intent(id="i1", capability="display.kpi_row")
        report = IntentCoverageValidator.check_coverage([intent], [])
        self.assertEqual(report.coverage, 0.0)
        self.assertEqual(len(report.missing), 1)

    def test_coverage_with_no_intents_no_contracts(self):
        report = IntentCoverageValidator.check_coverage([], [])
        self.assertEqual(report.coverage, 1.0)
        self.assertEqual(report.total_intents, 0)

    def test_decompose_no_match(self):
        intents = decompose_task("something completely unrelated")
        self.assertEqual(intents, [])


if __name__ == "__main__":
    unittest.main()
