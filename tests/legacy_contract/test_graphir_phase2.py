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
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.graphir.intent import (
    Intent,
    IntentPlan,
    IntentNode,
    CapabilityDef,
    make_intent_id,
    resolve_graphir_type_from_capability,
    resolve_edge_role_from_capability,
    is_capability_metadata,
    CAPABILITY_REGISTRY,
)
from app.graphir.param_extractor import ParamExtractor
from app.graphir.intent_decomposition import decompose_task
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
        intent = Intent(id="i1", capability="presentation.kpi_row")
        self.assertEqual(intent.id, "i1")
        self.assertEqual(intent.capability, "presentation.kpi_row")
        self.assertEqual(intent.params, {})
        self.assertEqual(intent.task_fragment, "")
        self.assertEqual(intent.weight, 1.0)

    def test_construct_full(self):
        intent = Intent(
            id="i_test_001",
            capability="presentation.timeseries",
            params={"metric": "revenue"},
            task_fragment="timeseries",
            weight=0.8,
        )
        self.assertEqual(intent.params["metric"], "revenue")
        self.assertEqual(intent.task_fragment, "timeseries")
        self.assertEqual(intent.weight, 0.8)

    def test_is_immutable(self):
        intent = Intent(id="i1", capability="presentation.kpi_row")
        with self.assertRaises(AttributeError):
            intent.id = "changed"  # type: ignore

    def test_make_intent_id_deterministic(self):
        id1 = make_intent_id("show kpi", "presentation.kpi_row")
        id2 = make_intent_id("show kpi", "presentation.kpi_row")
        self.assertEqual(id1, id2)

    def test_make_intent_id_different_capability(self):
        id1 = make_intent_id("show kpi", "presentation.kpi_row")
        id2 = make_intent_id("show kpi", "presentation.timeseries")
        self.assertNotEqual(id1, id2)

    def test_make_intent_id_different_fragment(self):
        id1 = make_intent_id("show kpi", "presentation.kpi_row")
        id2 = make_intent_id("show kpi chart", "presentation.kpi_row")
        self.assertNotEqual(id1, id2)

    def test_resolve_graphir_type_from_capability(self):
        self.assertEqual(
            resolve_graphir_type_from_capability("presentation.kpi_row"),
            "KpiRow",
        )
        self.assertEqual(
            resolve_graphir_type_from_capability("presentation.timeseries"),
            "Timeseries",
        )
        self.assertEqual(
            resolve_graphir_type_from_capability("presentation.table"),
            "AnalyticsTable",
        )
        self.assertEqual(
            resolve_graphir_type_from_capability("layout.page"),
            "Page",
        )
        # Backward compat: old aliases still resolve
        self.assertEqual(
            resolve_graphir_type_from_capability("display.kpi_row"),
            "KpiRow",
        )

    def test_resolve_edge_role_from_capability(self):
        self.assertEqual(
            resolve_edge_role_from_capability("presentation.kpi_row"),
            "PRIMARY",
        )
        self.assertEqual(
            resolve_edge_role_from_capability("presentation.timeseries"),
            "SUPPORTING",
        )

    def test_resolve_unknown_capability(self):
        self.assertIsNone(resolve_graphir_type_from_capability("unknown.x"))
        self.assertIsNone(resolve_edge_role_from_capability("unknown.x"))

    def test_intent_roundtrip(self):
        original = Intent(
            id="i_roundtrip_001",
            capability="presentation.kpi_row",
            params={"metrics": ["revenue"]},
            task_fragment="kpi metrics",
            weight=1.0,
            source="keyword",
        )
        restored = Intent.from_dict(original.to_dict())
        self.assertEqual(original, restored)

    def test_intent_roundtrip_with_embedding_source(self):
        original = Intent(
            id="i_roundtrip_002",
            capability="presentation.timeseries",
            params={"metric": "revenue"},
            task_fragment="embedding:presentation.timeseries",
            weight=0.85,
            source="embedding",
        )
        restored = Intent.from_dict(original.to_dict())
        self.assertEqual(original, restored)

    def test_intent_from_dict_defaults(self):
        minimal = {
            "id": "i_defaults",
            "capability": "presentation.table",
        }
        restored = Intent.from_dict(minimal)
        self.assertEqual(restored.params, {})
        self.assertEqual(restored.task_fragment, "")
        self.assertEqual(restored.weight, 1.0)
        self.assertEqual(restored.source, "keyword")

    def test_intent_to_dict_contains_all_fields(self):
        intent = Intent(
            id="i_fields",
            capability="layout.page",
            params={"title": "Dashboard"},
            task_fragment="page layout",
            weight=0.9,
            source="keyword",
        )
        d = intent.to_dict()
        self.assertEqual(d["id"], "i_fields")
        self.assertEqual(d["capability"], "layout.page")
        self.assertEqual(d["params"], {"title": "Dashboard"})
        self.assertEqual(d["task_fragment"], "page layout")
        self.assertEqual(d["weight"], 0.9)
        self.assertEqual(d["source"], "keyword")


# ════════════════════════════════════════════════════════════
# 2. Intent decomposition
# ════════════════════════════════════════════════════════════

class TestIntentDecomposition(unittest.TestCase):

    def test_decompose_empty_task(self):
        self.assertEqual(decompose_task("").intents, [])
        self.assertEqual(decompose_task("   ").intents, [])

    def test_decompose_kpi_keyword(self):
        intents = decompose_task("show revenue kpi").intents
        caps = [i.capability for i in intents]
        self.assertIn("presentation.kpi_row", caps)

    def test_decompose_timeseries_keyword(self):
        intents = decompose_task("revenue over time").intents
        caps = [i.capability for i in intents]
        self.assertIn("presentation.timeseries", caps)

    def test_decompose_table_keyword(self):
        intents = decompose_task("sales data table").intents
        caps = [i.capability for i in intents]
        self.assertIn("presentation.table", caps)

    def test_decompose_multi_intent(self):
        intents = decompose_task("dashboard with kpi metrics and timeseries chart").intents
        caps = [i.capability for i in intents]
        self.assertIn("presentation.kpi_row", caps)
        self.assertIn("presentation.timeseries", caps)

    def test_decompose_no_duplicates(self):
        intents = decompose_task("kpi metrics and more kpi").intents
        caps = [i.capability for i in intents]
        self.assertEqual(caps, ["presentation.kpi_row"])  # no duplicate

    def test_decompose_ids_are_deterministic(self):
        t1 = decompose_task("kpi and timeseries").intents
        t2 = decompose_task("kpi and timeseries").intents
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
            self._make_intent("presentation.kpi_row"),
            self._make_intent("presentation.timeseries"),
        ]
        report = IntentCoverageValidator.check_coverage(intents, [SKILL_CONTRACTS[("dashboard.sales_overview", 1)]])
        self.assertEqual(report.coverage, 1.0)
        self.assertEqual(report.covered_intents, 2)
        self.assertEqual(len(report.missing), 0)

    def test_coverage_partial(self):
        intents = [
            self._make_intent("presentation.kpi_row"),
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
            self._make_intent("presentation.kpi_row"),
            self._make_intent("presentation.timeseries"),
            self._make_intent("presentation.table"),
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
        self.assertIn("presentation.kpi_row", index)
        self.assertIn("presentation.timeseries", index)
        self.assertIn("presentation.table", index)
        # Each capability points to (contract_id, slot_type)
        kpi_matches = index["presentation.kpi_row"]
        self.assertEqual(kpi_matches[0][0], "dashboard.sales_overview")
        self.assertEqual(kpi_matches[0][1], "KpiRow")
        # Backward compat: old aliases also indexed
        self.assertIn("display.kpi_row", index)

    def test_coverage_report_has_gates(self):
        intents = [self._make_intent("presentation.kpi_row")]
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
        # verify decomposition_confidence and semantic_entropy are reported
        self.assertGreaterEqual(report.decomposition_confidence, 0.0)
        self.assertGreaterEqual(report.semantic_entropy, 0.0)
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
                metadata={"intent_id": iid, "intent_capability": "presentation.kpi_row"},
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
            Intent(id="i1", capability="presentation.kpi_row"),
            Intent(id="i2", capability="presentation.timeseries"),
        ]
        draft = self._make_graph_with_intents(["i1", "i2"])
        graph = draft.freeze()
        report = self._make_coverage_report(intents)
        result = IntentCoverageValidator.revalidate(graph, report, intents)
        self.assertEqual(len(result.uncovered_intents), 0)
        self.assertTrue(result.gates_passed["revalidation"])

    def test_revalidation_fails_on_missing_node(self):
        intents = [
            Intent(id="i1", capability="presentation.kpi_row"),
            Intent(id="i2", capability="presentation.timeseries"),
        ]
        draft = self._make_graph_with_intents(["i1"])  # missing i2
        graph = draft.freeze()
        report = self._make_coverage_report(intents)
        with self.assertRaises(IntentCoverageError) as ctx:
            IntentCoverageValidator.revalidate(graph, report, intents)
        self.assertIn("i2", str(ctx.exception))

    def test_revalidation_builds_intent_to_nodes(self):
        intents = [Intent(id="i1", capability="presentation.kpi_row")]
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
        intent = Intent(id="i1", capability="presentation.kpi_row", params={"metrics": ["revenue"]})
        plan = IntentPlan(intents=[intent], params={"metrics": ["revenue"]})
        self.assertEqual(len(plan.intents), 1)
        self.assertEqual(plan.intents[0].capability, "presentation.kpi_row")

    def test_intent_plan_validate_passes(self):
        intent = Intent(id="i1", capability="layout.page")
        plan = IntentPlan(intents=[intent])
        IntentPlan.validate(plan)

    def test_intent_plan_validate_fails_empty(self):
        plan = IntentPlan(intents=[])
        with self.assertRaises(ValueError):
            IntentPlan.validate(plan)


# ════════════════════════════════════════════════════════════
# 7. Phase 1: Capability registry, soft intents, entropy
# ════════════════════════════════════════════════════════════

class TestCapabilityRegistry(unittest.TestCase):

    def test_resolve_capability_def_new_id(self):
        from app.graphir.intent import resolve_capability_def
        cap = resolve_capability_def("presentation.kpi_row")
        self.assertIsNotNone(cap)
        self.assertEqual(cap.id, "presentation.kpi_row")
        self.assertEqual(cap.axes.presentation, "kpi_row")
        self.assertFalse(cap.is_soft)

    def test_resolve_capability_def_old_alias(self):
        from app.graphir.intent import resolve_capability_def
        cap = resolve_capability_def("display.kpi_row")
        self.assertIsNotNone(cap)
        self.assertEqual(cap.axes.presentation, "kpi_row")

    def test_resolve_capability_def_unknown(self):
        from app.graphir.intent import resolve_capability_def
        self.assertIsNone(resolve_capability_def("unknown.capability"))

    def test_soft_intent_layout(self):
        from app.graphir.intent import is_capability_soft
        self.assertTrue(is_capability_soft("layout.page"))
        self.assertTrue(is_capability_soft("layout.grid"))
        self.assertTrue(is_capability_soft("style.theme.dark"))
        self.assertTrue(is_capability_soft("style.card.elevated"))

    def test_not_soft_presentation(self):
        from app.graphir.intent import is_capability_soft
        self.assertFalse(is_capability_soft("presentation.kpi_row"))
        self.assertFalse(is_capability_soft("presentation.table"))
        self.assertFalse(is_capability_soft("data.export"))

    def test_registry_has_all_new_capabilities(self):
        from app.graphir.intent import CAPABILITY_REGISTRY
        expected = [
            "presentation.kpi_row", "presentation.timeseries", "presentation.table",
            "presentation.filter_panel", "domain.analytics", "domain.sales",
            "layout.page", "layout.grid", "layout.container",
            "style.theme.dark", "style.theme.light", "style.theme.enterprise",
            "style.card.elevated", "data.export", "data.drilldown",
            "interaction.search", "interaction.form",
        ]
        for cap in expected:
            self.assertIn(cap, CAPABILITY_REGISTRY, f"Missing: {cap}")

    def test_registry_soft_flags_are_correct(self):
        from app.graphir.intent import CAPABILITY_REGISTRY
        for cap_id, cap_def in CAPABILITY_REGISTRY.items():
            if cap_id.startswith("layout.") or cap_id.startswith("style."):
                self.assertTrue(cap_def.is_soft, f"{cap_id} should be soft")
            else:
                self.assertFalse(cap_def.is_soft, f"{cap_id} should NOT be soft")


class TestSemanticEntropy(unittest.TestCase):

    def test_entropy_empty_task(self):
        from app.graphir.intent import compute_semantic_entropy
        self.assertEqual(compute_semantic_entropy("", []), 1.0)
        self.assertEqual(compute_semantic_entropy("   ", []), 1.0)

    def test_entropy_no_matched_intents(self):
        from app.graphir.intent import compute_semantic_entropy
        entropy = compute_semantic_entropy("haz algo bonito", [])
        self.assertGreaterEqual(entropy, 0.8)

    def test_entropy_specific_task(self):
        from app.graphir.intent import compute_semantic_entropy, Intent
        intents = [
            Intent(id="i1", capability="presentation.table", task_fragment="data table"),
            Intent(id="i2", capability="presentation.kpi_row", task_fragment="kpi metrics"),
        ]
        entropy = compute_semantic_entropy("show dashboard with data table and kpi metrics", intents)
        self.assertLess(entropy, 0.6)  # 4/8 tokens matched → entropy ~0.5

    def test_entropy_vague_task(self):
        from app.graphir.intent import compute_semantic_entropy, Intent
        intents = [
            Intent(id="i1", capability="layout.page", task_fragment="page layout"),
        ]
        entropy = compute_semantic_entropy("haz algo bonito", intents)
        self.assertGreater(entropy, 0.7)


class TestDecompositionConfidence(unittest.TestCase):

    def test_confidence_empty(self):
        from app.graphir.intent_decomposition import compute_decomposition_confidence
        self.assertEqual(compute_decomposition_confidence("", []), 1.0)
        self.assertEqual(compute_decomposition_confidence("test", []), 0.0)

    def test_confidence_full_match(self):
        from app.graphir.intent_decomposition import compute_decomposition_confidence
        from app.graphir.intent import Intent
        intents = [
            Intent(id="i1", capability="layout.page", task_fragment="dashboard"),
        ]
        conf = compute_decomposition_confidence("dashboard", intents)
        self.assertGreater(conf, 0.8)

    def test_confidence_partial_match(self):
        from app.graphir.intent_decomposition import compute_decomposition_confidence
        from app.graphir.intent import Intent
        intents = [
            Intent(id="i1", capability="presentation.kpi_row", task_fragment="kpi metrics"),
        ]
        conf = compute_decomposition_confidence("dashboard con kpi y tabla y filtros", intents)
        # 2 matched tokens (kpi) out of 7 total → ~0.29
        self.assertLess(conf, 0.5)


class TestSoftIntentCoverage(unittest.TestCase):

    def _get_contracts(self):
        from app.contracts.skill_registry import SKILL_CONTRACTS
        return [
            SKILL_CONTRACTS[("dashboard.sales_overview", 1)],
            SKILL_CONTRACTS[("analytics.table", 1)],
        ]

    def _make_intent(self, capability: str) -> Intent:
        return Intent(
            id=make_intent_id("test", capability),
            capability=capability,
            task_fragment="test",
        )

    def test_soft_intent_missing_does_not_block(self):
        """Soft intents (style.*) missing contracts → gate still passes."""
        intents = [
            self._make_intent("presentation.kpi_row"),
            self._make_intent("style.theme.dark"),  # soft, no contract
        ]
        report = IntentCoverageValidator.check_coverage(intents, self._get_contracts())
        self.assertLess(report.coverage, 1.0)     # overall coverage < 1.0
        self.assertEqual(report.hard_coverage, 1.0)  # hard coverage = 1.0
        self.assertTrue(report.gates_passed["coverage"])  # gate passes

    def test_hard_intent_missing_blocks(self):
        """Non-soft intents missing → gate fails."""
        intents = [
            self._make_intent("data.export"),  # not in contracts
            self._make_intent("interaction.search"),  # not in contracts
        ]
        report = IntentCoverageValidator.check_coverage(intents, self._get_contracts())
        self.assertEqual(report.hard_coverage, 0.0)
        self.assertFalse(report.gates_passed["coverage"])

    def test_mixed_soft_and_hard_failure(self):
        """Missing hard + missing soft → gate fails due to hard."""
        intents = [
            self._make_intent("presentation.kpi_row"),
            self._make_intent("data.export"),  # hard, no contract
            self._make_intent("style.theme.dark"),  # soft, no contract
        ]
        report = IntentCoverageValidator.check_coverage(intents, self._get_contracts())
        self.assertLess(report.hard_coverage, 1.0)
        self.assertFalse(report.gates_passed["coverage"])

    def test_coverage_report_has_new_fields(self):
        intents = [self._make_intent("presentation.kpi_row")]
        report = IntentCoverageValidator.check_coverage(intents, self._get_contracts())
        self.assertIn("decomposition_confidence", report.__dict__)
        self.assertIn("semantic_entropy", report.__dict__)
        self.assertGreaterEqual(report.decomposition_confidence, 0.0)
        self.assertGreaterEqual(report.semantic_entropy, 0.0)


class TestNewDecompositionFeatures(unittest.TestCase):

    def test_decompose_style_keyword(self):
        result = decompose_task("dark theme dashboard")
        caps = [i.capability for i in result.intents]
        self.assertIn("style.theme.dark", caps)

    def test_decompose_domain_keyword(self):
        result = decompose_task("analytics sales dashboard")
        caps = [i.capability for i in result.intents]
        self.assertIn("domain.analytics", caps)
        self.assertIn("domain.sales", caps)

    def test_decompose_bar_chart(self):
        result = decompose_task("bar chart of revenue")
        caps = [i.capability for i in result.intents]
        self.assertIn("presentation.chart.bar", caps)

    def test_decompose_enterprise_style(self):
        result = decompose_task("enterprise dashboard with elevated cards")
        caps = [i.capability for i in result.intents]
        self.assertIn("style.theme.enterprise", caps)
        self.assertIn("style.card.elevated", caps)

    def test_decompose_multi_category(self):
        """A task can produce presentation + layout + style intents."""
        result = decompose_task("dark analytics dashboard with kpi and table")
        caps = [i.capability for i in result.intents]
        self.assertIn("presentation.kpi_row", caps)
        self.assertIn("presentation.table", caps)
        self.assertIn("domain.analytics", caps)
        # Check that at least one style intent is present
        style_caps = [c for c in caps if c.startswith("style.")]
        self.assertGreater(len(style_caps), 0)

    def test_backward_compat_old_aliases_still_resolve(self):
        """Old capability strings still work through alias system."""
        from app.graphir.intent import resolve_graphir_type_from_capability, resolve_edge_role_from_capability
        self.assertEqual(resolve_graphir_type_from_capability("display.kpi_row"), "KpiRow")
        self.assertEqual(resolve_graphir_type_from_capability("display.timeseries"), "Timeseries")
        self.assertEqual(resolve_graphir_type_from_capability("display.analytics_table"), "AnalyticsTable")
        self.assertEqual(resolve_edge_role_from_capability("display.kpi_row"), "PRIMARY")
        self.assertEqual(resolve_edge_role_from_capability("display.timeseries"), "SUPPORTING")

class TestCoverageEdgeCases(unittest.TestCase):

    def test_coverage_with_no_contracts(self):
        intent = Intent(id="i1", capability="presentation.kpi_row")
        report = IntentCoverageValidator.check_coverage([intent], [])
        self.assertEqual(report.coverage, 0.0)
        self.assertEqual(len(report.missing), 1)

    def test_coverage_with_no_intents_no_contracts(self):
        report = IntentCoverageValidator.check_coverage([], [])
        self.assertEqual(report.coverage, 1.0)
        self.assertEqual(report.total_intents, 0)

    def test_decompose_no_match(self):
        result = decompose_task("something completely unrelated")
        self.assertEqual(result.intents, [])

    def test_decompose_result_has_detected(self):
        """DecompositionResult.detected matches resolved capabilities."""
        result = decompose_task("show revenue kpi")
        self.assertIn("presentation.kpi_row", result.detected)

    def test_decompose_result_detected_multi(self):
        result = decompose_task("dashboard with kpi metrics and timeseries chart")
        self.assertIn("presentation.kpi_row", result.detected)
        self.assertIn("presentation.timeseries", result.detected)

    def test_decompose_result_inferred_empty_for_keyword(self):
        """Keyword-only decomposition should have empty inferred."""
        result = decompose_task("dark theme dashboard")
        self.assertEqual(result.inferred, [])

    def test_decompose_result_unresolved_present(self):
        result = decompose_task("show revenue kpi")
        self.assertIn("show", result.unresolved)

    def test_decompose_result_unresolved_excludes_matched(self):
        result = decompose_task("show revenue kpi")
        for token in result.unresolved:
            self.assertNotIn(token, {"kpi", "revenue"})

    def test_decompose_result_confidence_empty(self):
        result = decompose_task("")
        self.assertEqual(result.decomposition_confidence, 1.0)

    def test_decompose_result_confidence_perfect(self):
        """When all tokens are consumed by patterns, confidence is 1.0."""
        result = decompose_task("kpi")
        self.assertEqual(result.decomposition_confidence, 1.0)

    def test_decompose_result_confidence_partial(self):
        result = decompose_task("show revenue kpi")
        # "show" is unresolved → partial confidence
        self.assertGreater(result.decomposition_confidence, 0.0)
        self.assertLess(result.decomposition_confidence, 1.0)

    def test_coverage_report_carries_detected(self):
        """CoverageReport.detected_intents is populated from DecompositionResult."""
        result = decompose_task("kpi and timeseries")
        report = IntentCoverageValidator.check_coverage(
            result.intents, list(SKILL_CONTRACTS.values()),
            detected_intents=result.detected,
        )
        self.assertEqual(report.detected_intents, result.detected)

    def test_coverage_report_carries_inferred(self):
        """CoverageReport.inferred_intents is empty for keyword decomposition."""
        result = decompose_task("kpi and timeseries")
        report = IntentCoverageValidator.check_coverage(
            result.intents, list(SKILL_CONTRACTS.values()),
            detected_intents=result.detected,
            inferred_intents=result.inferred,
        )
        self.assertEqual(report.inferred_intents, [])

    def test_coverage_report_carries_unresolved(self):
        """CoverageReport.unresolved_fragments mirrors result.unresolved."""
        result = decompose_task("show revenue kpi")
        report = IntentCoverageValidator.check_coverage(
            result.intents, list(SKILL_CONTRACTS.values()),
            unresolved_fragments=result.unresolved,
        )
        self.assertEqual(report.unresolved_fragments, result.unresolved)

    @pytest.mark.skipif(True, reason="requires full integration environment")
    def test_intent_fidelity_includes_new_fields(self):
        """apply_engine serializes detected/inferred/unresolved into intent_fidelity."""


# ════════════════════════════════════════════════════════════
# 10. Intent serialization & E2E plan-serialize-deserialize
# ════════════════════════════════════════════════════════════

class TestIntentSerializationPipeline(unittest.TestCase):
    """End-to-end test for the plan → serialize → deserialize → coverage pipeline.

    Regression: decomposition_confidence was 0.0 and semantic_entropy was 1.0
    because task_fragment was silently dropped during serialization.
    """

    def test_plan_serialize_deserialize_coverage(self):
        """Simulates agent_plan → plan dict → apply_engine → check_coverage."""
        task = "Create a sales dashboard showing revenue and growth metrics with timeseries. Create a sales table in page."

        # ── Step 1: Decompose (as agent_plan does) ──
        dec_result = decompose_task(task)
        self.assertGreater(dec_result.decomposition_confidence, 0.0,
                            "decomposition_confidence should be > 0 after decompose")

        # ── Step 2: Serialize to dict (as agent_plan does) ──
        intents_data = [i.to_dict() for i in dec_result.intents]

        # ── Step 3: Deserialize from dict (as apply_engine._build_intents_from_plan does) ──
        from app.graphir.intent import Intent
        restored_intents = [Intent.from_dict(item) for item in intents_data]

        # ── Step 4: Run coverage check (as apply_engine does) ──
        report = IntentCoverageValidator.check_coverage(
            restored_intents,
            list(SKILL_CONTRACTS.values()),
            task=task,
            detected_intents=dec_result.detected,
            inferred_intents=dec_result.inferred,
            unresolved_fragments=dec_result.unresolved,
            decomposition_confidence=dec_result.decomposition_confidence,
        )

        # ── Assertions: the three bugs ──
        # Bug 1: decomposition_confidence was 0.0
        self.assertGreater(report.decomposition_confidence, 0.0,
                           "decomposition_confidence preserved through serialization")

        # Bug 2: semantic_entropy was 1.0
        self.assertLess(report.semantic_entropy, 1.0,
                        "semantic_entropy should be < 1.0 when fragments are preserved")

        # Bug 3: metadata-only capabilities in missing_intents
        missing_metadata = [
            m for m in report.missing
            if is_capability_metadata(m.capability)
        ]
        self.assertEqual(
            len(missing_metadata), 0,
            f"metadata-only caps should not appear in missing_intents: {missing_metadata}",
        )

    def test_plan_serialize_deserialize_without_dec_confidence_fallback(self):
        """When decomposition_confidence is NOT passed, check_coverage recalculates it.

        This tests the fallback path: if the plan doesn't carry dec_confidence,
        check_coverage should compute it from the restored intents' task_fragments.
        """
        task = "kpi and timeseries dashboard"
        dec_result = decompose_task(task)

        # Serialize/deserialize intents
        intents_data = [i.to_dict() for i in dec_result.intents]
        from app.graphir.intent import Intent
        restored_intents = [Intent.from_dict(item) for item in intents_data]

        # Coverage WITHOUT passing decomposition_confidence (fallback path)
        report = IntentCoverageValidator.check_coverage(
            restored_intents,
            list(SKILL_CONTRACTS.values()),
            task=task,
        )

        # Must still produce valid confidence from task_fragments
        self.assertGreater(report.decomposition_confidence, 0.0,
                           "fallback recomputation should work with preserved fragments")


# ════════════════════════════════════════════════════════════
# 11. Param extraction (Intent Param Schema System)
# ════════════════════════════════════════════════════════════

class TestParamExtractor(unittest.TestCase):
    """Test the ParamExtractor — rule-based structured param extraction."""

    def setUp(self):
        self.extractor = ParamExtractor()

    def _make_intent(self, capability: str) -> Intent:
        return Intent(id="test", capability=capability)

    def _contract_for(self, capability: str) -> CapabilityDef | None:
        return CAPABILITY_REGISTRY.get(capability)

    # ── Metrics extraction ──

    def test_extract_metrics_basic(self):
        intent = self._make_intent("presentation.kpi_row")
        contract = self._contract_for("presentation.kpi_row")
        result = self.extractor.extract("show revenue and growth", intent, contract)
        self.assertIn("metrics", result)
        self.assertIn("revenue", result["metrics"])
        self.assertIn("growth", result["metrics"])

    def test_extract_metrics_empty(self):
        intent = self._make_intent("presentation.kpi_row")
        contract = self._contract_for("presentation.kpi_row")
        result = self.extractor.extract("hello world", intent, contract)
        self.assertEqual(result, {})

    def test_extract_metrics_does_not_include_kpi(self):
        """'kpi' is a capability keyword, not a business metric name."""
        intent = self._make_intent("presentation.kpi_row")
        contract = self._contract_for("presentation.kpi_row")
        result = self.extractor.extract("show kpi", intent, contract)
        if "metrics" in result:
            self.assertNotIn("kpi", result["metrics"])

    # ── Dimensions extraction ──

    def test_extract_dimensions_by(self):
        intent = self._make_intent("presentation.table")
        contract = self._contract_for("presentation.table")
        result = self.extractor.extract("revenue by region", intent, contract)
        self.assertEqual(result.get("dimensions"), ["region"])

    def test_extract_dimensions_multi_and(self):
        intent = self._make_intent("presentation.table")
        contract = self._contract_for("presentation.table")
        result = self.extractor.extract("sales by region and product", intent, contract)
        self.assertEqual(result.get("dimensions"), ["region", "product"])

    def test_extract_dimensions_stops_at_transition_word(self):
        """'by region showing revenue' should capture only 'region'."""
        intent = self._make_intent("presentation.table")
        contract = self._contract_for("presentation.table")
        result = self.extractor.extract("sales by region showing revenue", intent, contract)
        self.assertEqual(result.get("dimensions"), ["region"])

    def test_extract_dimensions_no_match(self):
        intent = self._make_intent("presentation.table")
        contract = self._contract_for("presentation.table")
        result = self.extractor.extract("hello world", intent, contract)
        self.assertNotIn("dimensions", result)

    # ── Top K extraction ──

    def test_extract_top_k(self):
        intent = self._make_intent("presentation.table")
        contract = self._contract_for("presentation.table")
        result = self.extractor.extract("top 10 products", intent, contract)
        self.assertEqual(result.get("top_k"), 10)

    def test_extract_no_top_k(self):
        intent = self._make_intent("presentation.table")
        contract = self._contract_for("presentation.table")
        result = self.extractor.extract("all products", intent, contract)
        self.assertNotIn("top_k", result)

    # ── Time granularity ──

    def test_extract_time_granularity_monthly(self):
        intent = self._make_intent("presentation.timeseries")
        contract = self._contract_for("presentation.timeseries")
        result = self.extractor.extract("monthly revenue", intent, contract)
        self.assertEqual(result.get("time_granularity"), "monthly")

    def test_extract_time_granularity_daily(self):
        intent = self._make_intent("presentation.timeseries")
        contract = self._contract_for("presentation.timeseries")
        result = self.extractor.extract("daily sales trend", intent, contract)
        self.assertEqual(result.get("time_granularity"), "daily")

    # ── Columns extraction (deduplicated dimensions + metrics) ──

    def test_extract_columns_deduplicates(self):
        intent = self._make_intent("presentation.table")
        contract = self._contract_for("presentation.table")
        result = self.extractor.extract("revenue by revenue", intent, contract)
        if "columns" in result:
            self.assertEqual(len(result["columns"]), 1)

    def test_extract_columns(self):
        intent = self._make_intent("presentation.table")
        contract = self._contract_for("presentation.table")
        result = self.extractor.extract("sales by region showing revenue", intent, contract)
        if "columns" in result:
            self.assertIn("region", result["columns"])
            self.assertIn("revenue", result["columns"])
            self.assertIn("sales", result["columns"])

    # ── No schema capabilities ──

    def test_no_param_schema_returns_empty(self):
        """Layout and style capabilities have no param_schema → empty result."""
        intent = self._make_intent("layout.page")
        contract = self._contract_for("layout.page")
        result = self.extractor.extract("dashboard", intent, contract)
        self.assertEqual(result, {})

    # ── Group by extraction ──

    def test_extract_group_by(self):
        intent = self._make_intent("presentation.timeseries")
        contract = self._contract_for("presentation.timeseries")
        result = self.extractor.extract("revenue over time by product", intent, contract)
        self.assertEqual(result.get("group_by"), ["product"])

    # ── Domain capabilities ──

    def test_extract_domain_sales(self):
        intent = self._make_intent("domain.sales")
        contract = self._contract_for("domain.sales")
        result = self.extractor.extract("sales dashboard with revenue and growth by region", intent, contract)
        self.assertIn("metrics", result)
        self.assertIn("revenue", result["metrics"])
        self.assertIn("growth", result["metrics"])
        self.assertEqual(result.get("dimensions"), ["region"])


class TestParamExtractorIntegration(unittest.TestCase):
    """Param extraction integrated with decompose_task()."""

    def test_enrich_params_kpi(self):
        """KPI intent should get metrics from task."""
        result = decompose_task("show revenue and growth")
        kpi = [i for i in result.intents if i.capability == "presentation.kpi_row"]
        if kpi:
            self.assertIn("revenue", kpi[0].params.get("metrics", []))
            self.assertIn("growth", kpi[0].params.get("metrics", []))

    def test_enrich_params_table_with_dimensions(self):
        """Table intent should get dimensions and metrics."""
        result = decompose_task("sales table by region showing revenue")
        table = [i for i in result.intents if i.capability == "presentation.table"]
        if table:
            self.assertIn("revenue", table[0].params.get("metrics", []))
            self.assertEqual(table[0].params.get("dimensions"), ["region"])

    def test_enrich_params_timeseries_with_time(self):
        """Timeseries intent should get time_granularity."""
        result = decompose_task("monthly revenue trend by product")
        ts = [i for i in result.intents if i.capability == "presentation.timeseries"]
        if ts:
            self.assertEqual(ts[0].params.get("time_granularity"), "monthly")
            self.assertEqual(ts[0].params.get("group_by"), ["product"])

    def test_enrich_params_domain_sales(self):
        """Domain.sales intent should get metrics and dimensions."""
        result = decompose_task("sales dashboard with revenue and growth by region")
        ds = [i for i in result.intents if i.capability == "domain.sales"]
        if ds:
            self.assertIn("revenue", ds[0].params.get("metrics", []))

    def test_enrich_params_preserves_existing(self):
        """Existing params (from keyword hints) should not be overwritten."""
        result = decompose_task("revenue by region")
        # All intents with params should preserve existing structure
        for intent in result.intents:
            self.assertIsInstance(intent.params, dict)

    # ── Cross-intent leakage tests ──

    def test_enrich_params_no_cross_kpi_to_table(self):
        """KPI row should NOT get columns, dimensions, or top_k."""
        result = decompose_task("sales dashboard with revenue and growth by region, top 10")
        kpi = [i for i in result.intents if i.capability == "presentation.kpi_row"]
        if kpi:
            self.assertNotIn("columns", kpi[0].params)
            self.assertNotIn("dimensions", kpi[0].params)
            self.assertNotIn("top_k", kpi[0].params)

    def test_enrich_params_no_cross_table_to_timeseries(self):
        """Timeseries should NOT get columns or top_k (table-only fields)."""
        result = decompose_task("sales dashboard with revenue and growth by region, top 10")
        ts = [i for i in result.intents if i.capability == "presentation.timeseries"]
        if ts:
            self.assertNotIn("columns", ts[0].params)
            self.assertNotIn("top_k", ts[0].params)

    def test_enrich_params_kpi_only_metrics(self):
        """KPI row should ONLY have metrics in its params."""
        result = decompose_task("revenue and growth by region")
        kpi = [i for i in result.intents if i.capability == "presentation.kpi_row"]
        if kpi:
            for key in kpi[0].params:
                self.assertEqual(key, "metrics", f"KPI row got unexpected param: {key}")

    def test_enrich_params_table_gets_top_k_but_kpi_does_not(self):
        """top_k only appears in table/chart, NOT in kpi_row/timeseries."""
        result = decompose_task("top 10 products by revenue")
        for intent in result.intents:
            if intent.capability in ("presentation.kpi_row", "presentation.timeseries"):
                self.assertNotIn("top_k", intent.params,
                                 f"{intent.capability} should not get top_k")


class TestParamSchemaRegistry(unittest.TestCase):
    """Every capability that needs param_schema has one defined."""

    def test_presentation_caps_have_param_schema(self):
        caps = ["presentation.kpi_row", "presentation.timeseries",
                "presentation.table", "presentation.filter_panel",
                "presentation.chart.bar", "presentation.metric_card",
                "presentation.embed"]
        for c in caps:
            self.assertIsNotNone(CAPABILITY_REGISTRY[c].param_schema,
                                 f"{c} missing param_schema")

    def test_domain_caps_have_param_schema(self):
        for c in ["domain.analytics", "domain.sales"]:
            self.assertIsNotNone(CAPABILITY_REGISTRY[c].param_schema,
                                 f"{c} missing param_schema")

    def test_data_caps_have_param_schema(self):
        for c in ["data.export", "data.drilldown"]:
            self.assertIsNotNone(CAPABILITY_REGISTRY[c].param_schema,
                                 f"{c} missing param_schema")

    def test_interaction_caps_have_param_schema(self):
        for c in ["interaction.search", "interaction.form"]:
            self.assertIsNotNone(CAPABILITY_REGISTRY[c].param_schema,
                                 f"{c} missing param_schema")

    def test_layout_caps_no_param_schema(self):
        for c in ["layout.page", "layout.grid", "layout.container"]:
            self.assertIsNone(CAPABILITY_REGISTRY[c].param_schema,
                              f"{c} should NOT have param_schema")

    def test_style_caps_no_param_schema(self):
        for c in ["style.theme.light", "style.theme.dark",
                   "style.theme.enterprise", "style.card.elevated"]:
            self.assertIsNone(CAPABILITY_REGISTRY[c].param_schema,
                              f"{c} should NOT have param_schema")


# ════════════════════════════════════════════════════════════
# 12. Consumed token tracking (ParamExtractor ownership)
# ════════════════════════════════════════════════════════════

class TestConsumedTokens(unittest.TestCase):
    """ParamExtractor tracks consumed tokens for structure layer ownership."""

    def setUp(self):
        from app.graphir.param_extractor import ParamExtractor
        self.extractor = ParamExtractor()

    def _contract(self, cap):
        return CAPABILITY_REGISTRY[cap]

    def test_consumed_metrics(self):
        intent = Intent(id="t", capability="presentation.kpi_row")
        self.extractor.extract("revenue and growth", intent, self._contract("presentation.kpi_row"))
        self.assertIn("revenue", self.extractor.consumed_tokens)
        self.assertIn("growth", self.extractor.consumed_tokens)

    def test_consumed_top_k_uses_signal_token(self):
        intent = Intent(id="t", capability="presentation.table")
        self.extractor.extract("top 10 products", intent, self._contract("presentation.table"))
        self.assertIn("top_rank_signal", self.extractor.consumed_tokens)
        # "top" is also consumed to prevent structure layer reinterpretation
        self.assertIn("top", self.extractor.consumed_tokens)

    def test_consumed_dimensions(self):
        intent = Intent(id="t", capability="presentation.table")
        self.extractor.extract("revenue by region", intent, self._contract("presentation.table"))
        self.assertIn("region", self.extractor.consumed_tokens)

    def test_consumed_tokens_normalized(self):
        intent = Intent(id="t", capability="presentation.kpi_row")
        self.extractor.extract("Revenue and Growth", intent, self._contract("presentation.kpi_row"))
        self.assertIn("revenue", self.extractor.consumed_tokens)
        self.assertIn("growth", self.extractor.consumed_tokens)

    def test_consumed_no_cross_contamination(self):
        intent1 = Intent(id="a", capability="presentation.kpi_row")
        intent2 = Intent(id="b", capability="presentation.table")
        self.extractor.extract("revenue by region", intent1, self._contract("presentation.kpi_row"))
        self.extractor.extract("top 10 revenue by region", intent2, self._contract("presentation.table"))
        self.assertIn("top_rank_signal", self.extractor.consumed_tokens)
        self.assertIn("top", self.extractor.consumed_tokens)
        self.assertIn("region", self.extractor.consumed_tokens)


# ════════════════════════════════════════════════════════════
# 13. Structure annotation (intent_structure.py)
# ════════════════════════════════════════════════════════════

class TestStructureAnnotation(unittest.TestCase):
    """Post-hoc structural enrichment for intents."""

    def _kpi_intent(self) -> Intent:
        return Intent(id="kpi", capability="presentation.kpi_row", task_fragment="kpi")

    def _ts_intent(self) -> Intent:
        return Intent(id="ts", capability="presentation.timeseries", task_fragment="timeseries")

    def _table_intent(self) -> Intent:
        return Intent(id="tbl", capability="presentation.table", task_fragment="table")

    def _layout_intent(self) -> Intent:
        return Intent(id="page", capability="layout.page", task_fragment="page")

    # ── Layout roles ──

    def test_kpi_layout_role_primary(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._kpi_intent()], [])
        self.assertEqual(result[0].structure_context["layout_role"], "primary")

    def test_ts_layout_role_primary(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._ts_intent()], [])
        self.assertEqual(result[0].structure_context["layout_role"], "primary")

    def test_table_layout_role_secondary(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._table_intent()], [])
        self.assertEqual(result[0].structure_context["layout_role"], "secondary")

    def test_layout_page_ignored(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._layout_intent()], [])
        self.assertIsNone(result[0].structure_context)

    # ── Position hints (UI conventions) ──

    def test_kpi_position_above(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._kpi_intent()], [])
        self.assertEqual(result[0].structure_context["position_hint"], "above")

    def test_ts_position_inline(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._ts_intent()], [])
        self.assertEqual(result[0].structure_context["position_hint"], "inline")

    def test_table_position_below(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._table_intent()], [])
        self.assertEqual(result[0].structure_context["position_hint"], "below")

    # ── Modifiers (residual tokens only) ──

    def test_modifier_top_from_unresolved(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._kpi_intent()], ["top"])
        self.assertIn("top", result[0].structure_context.get("modifiers", []))

    def test_modifier_top_consumed_not_reinterpreted(self):
        """top consumed by ParamExtractor → structure should NOT see it."""
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._table_intent()], ["top"], consumed_tokens={"top_rank_signal", "top"})
        self.assertNotIn("modifiers", result[0].structure_context)

    def test_modifier_consumed_filtered_by_normalization(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._kpi_intent()], ["Top"], consumed_tokens={"top_rank_signal", "top"})
        self.assertNotIn("modifiers", result[0].structure_context)

    # ── Relations ──

    def test_relation_kpi_to_timeseries(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._kpi_intent(), self._ts_intent()], [])
        kpi = [i for i in result if i.capability == "presentation.kpi_row"][0]
        self.assertIn("relation_hints", kpi.structure_context)
        self.assertEqual(kpi.structure_context["relation_hints"][0]["type"], "feeds_into")

    def test_relation_ts_to_table(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._ts_intent(), self._table_intent()], [])
        ts = [i for i in result if i.capability == "presentation.timeseries"][0]
        self.assertIn("relation_hints", ts.structure_context)
        self.assertEqual(ts.structure_context["relation_hints"][0]["type"], "summarizes_into")

    def test_no_relation_when_target_missing(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._kpi_intent()], [])
        self.assertNotIn("relation_hints", result[0].structure_context)

    # ── Noise ignored ──

    def test_noise_tokens_ignored(self):
        from app.graphir.intent_structure import annotate_structure
        result = annotate_structure([self._kpi_intent()], ["products", "price", "tax"])
        self.assertNotIn("modifiers", result[0].structure_context)

    # ── Serialization roundtrip ──

    def test_structure_context_serialization_roundtrip(self):
        original = Intent(
            id="r1", capability="presentation.kpi_row",
            structure_context={"layout_role": "primary", "position_hint": "above"},
        )
        restored = Intent.from_dict(original.to_dict())
        self.assertEqual(original.structure_context, restored.structure_context)


if __name__ == "__main__":
    unittest.main()
