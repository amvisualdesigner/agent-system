"""Tests for reconciliation layer — SemanticResolution + reconcile().

Covers:
  - SemanticResolution dataclass + from_skillir()
  - reconcile() with full frame
  - Explicit vs explicit conflict → hard fail
  - Explicit vs SkillIR → explicit wins
  - Inferred merge
  - No frame fallback
  - No contract_id → hard fail
  - Run 1 + Run 2 scenarios from instrumented run
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.contracts.semantic_resolution import (
    SemanticResolution,
    SemanticConflictError,
)
from app.contracts.skill_ir import SkillIR
from app.engine.reconciliation import reconcile


def _frame(
    constraints: list | None = None,
    actions: list | None = None,
    confidence: float = 0.7,
) -> dict:
    return {
        "actions": actions or [],
        "objects": [],
        "constraints": constraints or [],
        "confidence": confidence,
        "missing_info": [],
    }


def _skill_ir(
    contract_id: str = "dashboard.sales_overview",
    params: dict | None = None,
    confidence: float = 0.8,
) -> SkillIR:
    return SkillIR(
        contract_id=contract_id,
        confidence=confidence,
        params=params or {},
        version=1,
    )


class TestSemanticResolution(unittest.TestCase):
    """SemanticResolution dataclass + from_skillir()."""

    def test_from_skillir_creates_resolution(self):
        sir = _skill_ir(params={"metrics": ["revenue"]})
        res = SemanticResolution.from_skillir(sir)
        self.assertEqual(res.contract_id, "dashboard.sales_overview")
        self.assertEqual(res.params["metrics"], ["revenue"])
        self.assertEqual(res.param_provenance["metrics"], "skillir_proposed")
        self.assertEqual(res.confidence, 0.8)

    def test_from_skillir_raises_on_no_contract(self):
        sir = _skill_ir(contract_id=None)
        with self.assertRaises(SemanticConflictError):
            SemanticResolution.from_skillir(sir)

    def test_resolution_has_all_fields(self):
        res = SemanticResolution(
            contract_id="test",
            contract_version=1,
            params={"x": 1},
            param_provenance={"x": "user_explicit"},
            confidence=0.9,
            resolution_trace=["test"],
        )
        self.assertEqual(res.contract_id, "test")
        self.assertEqual(res.params["x"], 1)
        self.assertEqual(len(res.resolution_trace), 1)


class TestReconcileNoFrame(unittest.TestCase):
    """reconcile() con frame_dict=None."""

    def test_no_frame_falls_back_to_skillir(self):
        sir = _skill_ir(params={"metrics": ["revenue"]})
        res = reconcile(None, sir)
        self.assertEqual(res.params["metrics"], ["revenue"])
        self.assertEqual(res.param_provenance["metrics"], "skillir_proposed")

    def test_no_frame_no_contract_raises(self):
        sir = _skill_ir(contract_id=None)
        with self.assertRaises(SemanticConflictError):
            reconcile(None, sir)


class TestReconcileExplicit(unittest.TestCase):
    """Regla 1-2: Explicit constraints."""

    def test_explicit_override_skillir(self):
        """Explicit user constraint overrides SkillIR proposal."""
        fr = _frame(constraints=[
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
        ])
        sir = _skill_ir(params={"metrics": ["revenue", "growth"]})
        res = reconcile(fr, sir)
        self.assertEqual(res.params["metrics"], ["net_revenue"])
        self.assertEqual(res.param_provenance["metrics"], "user_explicit")

    def test_explicit_adds_new_param(self):
        """Explicit constraint adds a param SkillIR didn't have."""
        fr = _frame(constraints=[
            {"param": "time_granularity", "value": "monthly", "source": "explicit"},
        ])
        sir = _skill_ir(params={"metrics": ["revenue"]})
        res = reconcile(fr, sir)
        self.assertIn("time_granularity", res.params)
        self.assertEqual(res.params["time_granularity"], "monthly")
        # SkillIR.params NO entran en resolution (Rule 5 eliminada)
        self.assertNotIn("metrics", res.params)

    def test_explicit_vs_explicit_same_value_ok(self):
        """Same explicit value for same param is not a conflict."""
        fr = _frame(constraints=[
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
        ])
        sir = _skill_ir(params={})
        res = reconcile(fr, sir)
        self.assertEqual(res.params["metrics"], ["net_revenue"])

    def test_explicit_vs_explicit_different_value_raises(self):
        """Two different explicit values for same param → hard fail."""
        fr = _frame(constraints=[
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
            {"param": "metrics", "value": ["revenue"], "source": "explicit"},
        ])
        sir = _skill_ir(params={})
        with self.assertRaises(SemanticConflictError) as ctx:
            reconcile(fr, sir)
        err = str(ctx.exception)
        self.assertIn("metrics", err)
        self.assertIn("net_revenue", err)
        self.assertIn("revenue", err)

    def test_explicit_vs_skillir_trace(self):
        """Explicit vs SkillIR conflict is traced, not errored."""
        fr = _frame(constraints=[
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
        ])
        sir = _skill_ir(params={"metrics": ["revenue", "ratio"]})
        res = reconcile(fr, sir)
        # Explicit wins
        self.assertEqual(res.params["metrics"], ["net_revenue"])
        # Trace captures the conflict
        traces = [t for t in res.resolution_trace if "override" in t]
        self.assertGreaterEqual(len(traces), 1, "Should have override trace")
        self.assertIn("ratio", traces[0], "Trace should mention what was overridden")


class TestReconcileInferred(unittest.TestCase):
    """Regla 4: Inferred constraints."""

    def test_inferred_merges_when_no_explicit(self):
        fr = _frame(constraints=[
            {"param": "time_granularity", "value": "monthly", "source": "inferred"},
        ])
        sir = _skill_ir(params={"metrics": ["revenue"]})
        res = reconcile(fr, sir)
        self.assertEqual(res.params["time_granularity"], "monthly")
        self.assertEqual(res.param_provenance["time_granularity"], "user_inferred")

    def test_inferred_skipped_when_explicit_exists(self):
        fr = _frame(constraints=[
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
            {"param": "metrics", "value": ["revenue"], "source": "inferred"},
        ])
        sir = _skill_ir(params={})
        res = reconcile(fr, sir)
        self.assertEqual(res.params["metrics"], ["net_revenue"])


class TestReconcileSkillIR(unittest.TestCase):
    """Rule 5 eliminada: SkillIR.params ya NO entran en resolution.

    SkillIR solo aporta contract_id, version, confidence.
    Los params vienen de frame constraints + safe defaults en complete_structure.
    """

    def test_skillir_params_not_injected(self):
        """SkillIR.params no deben aparecer en resolution si frame no los cubre."""
        fr = _frame(constraints=[
            {"param": "time_granularity", "value": "monthly", "source": "explicit"},
        ])
        sir = _skill_ir(params={
            "metrics": ["revenue", "growth"],
            "timeseries_metric": "revenue",
        })
        res = reconcile(fr, sir)
        # Frame explicit está presente
        self.assertEqual(res.params["time_granularity"], "monthly")
        self.assertEqual(res.param_provenance["time_granularity"], "user_explicit")
        # SkillIR params NO deben aparecer
        self.assertNotIn("metrics", res.params)
        self.assertNotIn("timeseries_metric", res.params)


class TestReconcileRun1(unittest.TestCase):
    """Run 1: Modify table + tfoot + buttons."""

    def test_run1_reconciliation(self):
        fr = _frame(
            actions=[
                {"verb": "add", "object": ""},
                {"verb": "modify", "object": "the"},
            ],
            constraints=[],
            confidence=0.506,
        )
        sir = _skill_ir(
            contract_id="analytics.table",
            params={"columns": ["Metric", "Value"]},
            confidence=0.7,
        )
        res = reconcile(fr, sir)
        self.assertEqual(res.contract_id, "analytics.table")
        # SkillIR.params ya NO entran (Rule 5 eliminada)
        self.assertNotIn("columns", res.params)
        self.assertGreaterEqual(len(res.resolution_trace), 1)
        # No explicit constraints, so no override
        self.assertGreaterEqual(len(res.resolution_trace), 1)


class TestReconcileRun2(unittest.TestCase):
    """Run 2: Dashboard + KPI override + retention + monthly + grid."""

    def setUp(self):
        self.frame = _frame(
            actions=[
                {"verb": "create", "object": "a"},
                {"verb": "override", "object": "to"},
            ],
            constraints=[
                {"param": "metrics", "value": ["net_revenue"], "source": "explicit",
                 "confidence": 0.9},
                {"param": "time_granularity", "value": "monthly", "source": "explicit",
                 "confidence": 0.85},
                {"param": "mentioned_metrics",
                 "value": ["revenue", "net_revenue", "retention"], "source": "explicit",
                 "confidence": 0.7},
            ],
            confidence=0.703,
        )
        self.skill_ir = _skill_ir(
            contract_id="dashboard.sales_overview",
            params={
                "metrics": ["revenue", "growth", "retention", "churn"],
                "timeseries_metric": "revenue",
            },
            confidence=0.85,
        )

    def test_metrics_override(self):
        """metrics debe ser net_revenue del usuario, NO el mix contaminado."""
        res = reconcile(self.frame, self.skill_ir)
        self.assertEqual(
            res.params["metrics"],
            ["net_revenue"],
            "metrics should be user_explicit override, not SkillIR mix",
        )
        self.assertEqual(res.param_provenance["metrics"], "user_explicit")

    def test_ratio_not_in_params(self):
        """'ratio' no debe aparecer en los params reconciliados."""
        res = reconcile(self.frame, self.skill_ir)
        params_str = str(res.params).lower()
        self.assertNotIn(
            "ratio", params_str,
            "'ratio' should NOT appear in reconciled params"
        )

    def test_time_granularity_from_frame(self):
        """time_granularity monthly del usuario debe preservarse."""
        res = reconcile(self.frame, self.skill_ir)
        self.assertEqual(res.params["time_granularity"], "monthly")

    def test_timeseries_metric_not_injected(self):
        """timeseries_metric de SkillIR ya NO entra en resolution (Rule 5 eliminada)."""
        res = reconcile(self.frame, self.skill_ir)
        self.assertNotIn("timeseries_metric", res.params)

    def test_confidence_is_min(self):
        """Confianza reconciliada es el mínimo entre frame y skill_ir."""
        res = reconcile(self.frame, self.skill_ir)
        self.assertAlmostEqual(res.confidence, min(0.703, 0.85))

    def test_override_trace_present(self):
        """Trace debe documentar que SkillIR metrics fue overridden."""
        res = reconcile(self.frame, self.skill_ir)
        traces = " ".join(res.resolution_trace)
        self.assertIn("override", traces,
                       "Trace should mention override of SkillIR params")


class TestReconcileEdgeCases(unittest.TestCase):
    """Edge cases for reconciliation."""

    def test_empty_constraints(self):
        fr = _frame(constraints=[])
        sir = _skill_ir(params={"metrics": ["revenue"]})
        res = reconcile(fr, sir)
        # SkillIR.params ya NO entran (Rule 5 eliminada)
        self.assertNotIn("metrics", res.params)

    def test_frame_without_skillir_params(self):
        fr = _frame(constraints=[
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
        ])
        sir = _skill_ir(params={})
        res = reconcile(fr, sir)
        self.assertEqual(res.params["metrics"], ["net_revenue"])

    def test_no_actions_no_crash(self):
        fr = _frame(constraints=[], actions=None)
        sir = _skill_ir(params={"x": 1})
        res = reconcile(fr, sir)
        # SkillIR.params ya NO entran (Rule 5 eliminada)
        self.assertNotIn("x", res.params)
        self.assertEqual(res.contract_id, "dashboard.sales_overview")

    def test_explicit_with_none_value(self):
        fr = _frame(constraints=[
            {"param": "metrics", "value": None, "source": "explicit"},
        ])
        sir = _skill_ir(params={"metrics": ["revenue"]})
        res = reconcile(fr, sir)
        self.assertIsNone(res.params["metrics"])


if __name__ == "__main__":
    unittest.main()
