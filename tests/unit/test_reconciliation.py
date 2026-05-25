"""Tests for reconciliation layer — SemanticResolution + reconcile().

Covers:
  - SemanticResolution dataclass (pure semantic params)
  - ContractResolution dataclass (contract-validated params)
  - reconcile() with full frame
  - Explicit vs explicit conflict → hard fail
  - Inferred merge
  - No frame → empty resolution
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
from app.contracts.contract_resolution import ContractResolution
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
    """SemanticResolution dataclass — pure semantic params only."""

    def test_creates_with_semantic_params(self):
        res = SemanticResolution(
            semantic_params={"mentioned_metrics": ["revenue"]},
            semantic_provenance={"mentioned_metrics": "user_explicit"},
            confidence=0.7,
        )
        self.assertEqual(res.semantic_params["mentioned_metrics"], ["revenue"])
        self.assertEqual(res.semantic_provenance["mentioned_metrics"], "user_explicit")
        self.assertEqual(res.confidence, 0.7)

    def test_empty_params_by_default(self):
        res = SemanticResolution(
            semantic_params={},
            semantic_provenance={},
            confidence=0.0,
        )
        self.assertEqual(res.semantic_params, {})
        self.assertEqual(res.semantic_provenance, {})

    def test_resolution_trace(self):
        res = SemanticResolution(
            semantic_params={"time_granularity": "monthly"},
            semantic_provenance={"time_granularity": "user_explicit"},
            confidence=0.9,
            resolution_trace=["[explicit] time_granularity=monthly"],
        )
        self.assertEqual(len(res.resolution_trace), 1)
        self.assertIn("monthly", res.resolution_trace[0])


class TestContractResolution(unittest.TestCase):
    """ContractResolution dataclass — contract-validated params."""

    def test_from_skillir_empty_contract(self):
        """Without contract, just pass through SkillIR params."""
        sir = _skill_ir(params={"metrics": ["net_revenue"]})
        res = ContractResolution.from_skillir(sir)
        self.assertEqual(res.contract_params["metrics"], ["net_revenue"])
        self.assertEqual(res.contract_provenance["metrics"], "skillir_proposed")

    def test_from_skillir_applies_defaults(self):
        """Contract defaults applied for optional params not in SkillIR."""
        from app.contracts.skill_registry import get_contract
        contract = get_contract("dashboard.sales_overview", 1)
        sir = _skill_ir(params={"metrics": ["net_revenue"]})
        res = ContractResolution.from_skillir(sir, contract)
        # metrics from SkillIR
        self.assertEqual(res.contract_params["metrics"], ["net_revenue"])
        # timeseries_metric default from contract
        self.assertIn("timeseries_metric", res.contract_params)
        self.assertEqual(res.contract_params["timeseries_metric"], "revenue")
        self.assertEqual(res.contract_provenance["timeseries_metric"], "contract_default")

    def test_from_skillir_overrides_default(self):
        """SkillIR value overrides contract default."""
        from app.contracts.skill_registry import get_contract
        contract = get_contract("dashboard.sales_overview", 1)
        sir = _skill_ir(
            params={"metrics": ["net_revenue"], "timeseries_metric": "net_revenue"},
        )
        res = ContractResolution.from_skillir(sir, contract)
        self.assertEqual(res.contract_params["timeseries_metric"], "net_revenue")
        self.assertEqual(res.contract_provenance["timeseries_metric"], "skillir_proposed")


class TestReconcileNoFrame(unittest.TestCase):
    """reconcile() con frame_dict=None."""

    def test_no_frame_returns_empty_resolution(self):
        res = reconcile(None)
        self.assertEqual(res.semantic_params, {})
        self.assertEqual(res.semantic_provenance, {})
        self.assertEqual(res.confidence, 0.0)


class TestReconcileExplicit(unittest.TestCase):
    """Regla 1-2: Explicit constraints."""

    def test_explicit_adds_param(self):
        fr = _frame(constraints=[
            {"param": "time_granularity", "value": "monthly", "source": "explicit"},
        ])
        res = reconcile(fr)
        self.assertIn("time_granularity", res.semantic_params)
        self.assertEqual(res.semantic_params["time_granularity"], "monthly")
        self.assertEqual(res.semantic_provenance["time_granularity"], "user_explicit")

    def test_explicit_vs_explicit_same_value_ok(self):
        """Same explicit value for same param is not a conflict."""
        fr = _frame(constraints=[
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
        ])
        res = reconcile(fr)
        self.assertEqual(res.semantic_params["metrics"], ["net_revenue"])

    def test_explicit_vs_explicit_different_value_raises(self):
        """Two different explicit values for same param → hard fail."""
        fr = _frame(constraints=[
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
            {"param": "metrics", "value": ["revenue"], "source": "explicit"},
        ])
        with self.assertRaises(SemanticConflictError) as ctx:
            reconcile(fr)
        err = str(ctx.exception)
        self.assertIn("metrics", err)
        self.assertIn("net_revenue", err)
        self.assertIn("revenue", err)

    def test_mentioned_metrics_from_explicit(self):
        """mentioned_metrics is a semantic observation, not a structural param."""
        fr = _frame(constraints=[
            {"param": "mentioned_metrics", "value": ["revenue", "net_revenue"],
             "source": "explicit"},
        ])
        res = reconcile(fr)
        self.assertIn("mentioned_metrics", res.semantic_params)
        self.assertEqual(res.semantic_params["mentioned_metrics"],
                         ["revenue", "net_revenue"])


class TestReconcileInferred(unittest.TestCase):
    """Regla 3: Inferred constraints."""

    def test_inferred_merges_when_no_explicit(self):
        fr = _frame(constraints=[
            {"param": "time_granularity", "value": "monthly", "source": "inferred"},
        ])
        res = reconcile(fr)
        self.assertEqual(res.semantic_params["time_granularity"], "monthly")
        self.assertEqual(res.semantic_provenance["time_granularity"], "user_inferred")

    def test_inferred_skipped_when_explicit_exists(self):
        fr = _frame(constraints=[
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
            {"param": "metrics", "value": ["revenue"], "source": "inferred"},
        ])
        res = reconcile(fr)
        self.assertEqual(res.semantic_params["metrics"], ["net_revenue"])

    def test_inferred_race_trace(self):
        """Inferred conflict is traced, not errored."""
        fr = _frame(constraints=[
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
            {"param": "metrics", "value": ["revenue"], "source": "inferred"},
        ])
        res = reconcile(fr)
        traces = " ".join(res.resolution_trace)
        self.assertIn("skip", traces.lower())


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
        res = reconcile(fr)
        self.assertEqual(res.semantic_params, {})
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

    def test_metrics_override(self):
        """metrics debe ser net_revenue del usuario."""
        res = reconcile(self.frame)
        self.assertEqual(
            res.semantic_params["metrics"],
            ["net_revenue"],
            "metrics should be user_explicit override",
        )
        self.assertEqual(res.semantic_provenance["metrics"], "user_explicit")

    def test_time_granularity_from_frame(self):
        """time_granularity monthly del usuario debe preservarse."""
        res = reconcile(self.frame)
        self.assertEqual(res.semantic_params["time_granularity"], "monthly")

    def test_mentioned_metrics_present(self):
        """mentioned_metrics debe estar en semantic_params como observación."""
        res = reconcile(self.frame)
        self.assertIn("mentioned_metrics", res.semantic_params)
        self.assertEqual(
            res.semantic_params["mentioned_metrics"],
            ["revenue", "net_revenue", "retention"],
        )


class TestReconcileEdgeCases(unittest.TestCase):
    """Edge cases for reconciliation."""

    def test_empty_constraints(self):
        fr = _frame(constraints=[])
        res = reconcile(fr)
        self.assertEqual(res.semantic_params, {})

    def test_frame_without_skillir_does_not_affect(self):
        """Frame without skill_ir still produces semantic params."""
        fr = _frame(constraints=[
            {"param": "metrics", "value": ["net_revenue"], "source": "explicit"},
        ])
        res = reconcile(fr)
        self.assertEqual(res.semantic_params["metrics"], ["net_revenue"])

    def test_no_actions_no_crash(self):
        fr = _frame(constraints=[], actions=None)
        res = reconcile(fr)
        self.assertEqual(res.semantic_params, {})

    def test_explicit_with_none_value(self):
        fr = _frame(constraints=[
            {"param": "metrics", "value": None, "source": "explicit"},
        ])
        res = reconcile(fr)
        self.assertIsNone(res.semantic_params["metrics"])

    def test_contract_resolution_preserves_skillir_params(self):
        """ContractResolution.from_skillir preserva params de SkillIR."""
        sir = _skill_ir(
            params={"metrics": ["net_revenue"], "timeseries_metric": "net_revenue"},
        )
        res = ContractResolution.from_skillir(sir)
        self.assertIn("metrics", res.contract_params)
        self.assertIn("timeseries_metric", res.contract_params)
        self.assertEqual(res.contract_params["metrics"], ["net_revenue"])
        self.assertEqual(res.contract_params["timeseries_metric"], "net_revenue")

    def test_semantic_resolution_never_has_contract_params(self):
        """SemanticResolution NEVER contains contract-level params like timeseries_metric."""
        fr = _frame(constraints=[
            {"param": "time_granularity", "value": "monthly", "source": "explicit"},
        ])
        res = reconcile(fr)
        self.assertNotIn("timeseries_metric", res.semantic_params)
        self.assertNotIn("metrics", res.semantic_params)


if __name__ == "__main__":
    unittest.main()
