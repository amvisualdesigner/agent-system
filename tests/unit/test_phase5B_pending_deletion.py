"""Phase 5B unit tests: PendingDeletion, confirmed_deletions gate."""
from __future__ import annotations

import pytest

from app.intent.models import PendingDeletion
from app.engine.structural_completion import (
    CompletionMode,
    StructuralIR,
    ResolvedCapability,
    DELETE,
    KEEP,
)


# ─── Tests: PendingDeletion dataclass ───


class TestPendingDeletionModel:
    """PendingDeletion solo tiene capability e instance_hint — NO paths."""

    def test_fields(self):
        pd = PendingDeletion(capability="presentation.kpi_row")
        assert pd.capability == "presentation.kpi_row"
        assert pd.instance_hint is None

    def test_with_instance_hint(self):
        pd = PendingDeletion(capability="presentation.chart.bar", instance_hint="linechart")
        assert pd.capability == "presentation.chart.bar"
        assert pd.instance_hint == "linechart"

    def test_no_path_field(self):
        pd = PendingDeletion(capability="presentation.kpi_row")
        assert not hasattr(pd, "paths")
        assert not hasattr(pd, "confirmed")
        assert not hasattr(pd, "file_path")


# ─── Tests: pending_deletions property in StructuralIR ───


class TestPendingDeletionsProperty:
    """StructuralIR.pending_deletions deriva de capabilities con action=DELETE."""

    def _make_cap(self, name, action, instance_hint=None):
        return ResolvedCapability(
            name=name, params={}, mode=CompletionMode.SAFE_SKIP,
            action=action, instance_hint=instance_hint,
        )

    def test_derives_from_capabilities(self):
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=(
                self._make_cap("presentation.kpi_row", DELETE),
                self._make_cap("layout.page", KEEP),
                self._make_cap("presentation.chart.bar", DELETE),
            ),
            param_provenance={}, confidence=1.0,
        )

        pending = ir.pending_deletions
        assert len(pending) == 2
        assert pending[0].capability == "presentation.kpi_row"
        assert pending[1].capability == "presentation.chart.bar"

    def test_empty_when_no_deletions(self):
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=(self._make_cap("presentation.kpi_row", KEEP),),
            param_provenance={}, confidence=1.0,
        )
        assert ir.pending_deletions == ()

    def test_includes_instance_hint(self):
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=(
                self._make_cap("presentation.chart.bar", DELETE, instance_hint="linechart"),
            ),
            param_provenance={}, confidence=1.0,
        )
        assert len(ir.pending_deletions) == 1
        assert ir.pending_deletions[0].instance_hint == "linechart"

    def test_property_is_tuple_of_pending_deletion(self):
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=(self._make_cap("presentation.kpi_row", DELETE),),
            param_provenance={}, confidence=1.0,
        )
        pending = ir.pending_deletions
        assert isinstance(pending, tuple)
        assert isinstance(pending[0], PendingDeletion)

    def test_frozen_property(self):
        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=(), param_provenance={}, confidence=1.0,
        )
        with pytest.raises(Exception):
            ir.pending_deletions = ()  # type: ignore


# ─── Tests: confirmed_deletions gate in apply_engine ───


class TestConfirmedDeletionsGate:
    """apply_engine rechaza si pending deletions no estan confirmadas."""

    def _make_ir_with_pending(self):
        return StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=(
                ResolvedCapability(name="presentation.kpi_row", params={},
                                   mode=CompletionMode.SAFE_SKIP, action=DELETE),
            ),
            param_provenance={}, confidence=1.0,
        )

    def test_rejects_unconfirmed(self):
        """confirmed_deletions=[] con pending → rejected."""
        from app.engine.apply_engine import _validate_confirmed_deletions

        ir = self._make_ir_with_pending()
        result = _validate_confirmed_deletions(ir, [])
        assert result is not None
        assert result["execution"]["status"] == "rejected"
        assert "unconfirmed_deletion" in result["execution"]["reason"]

    def test_accepts_with_confirmed(self):
        """confirmed_deletions=["presentation.kpi_row"] → ok (None)."""
        from app.engine.apply_engine import _validate_confirmed_deletions

        ir = self._make_ir_with_pending()
        result = _validate_confirmed_deletions(ir, ["presentation.kpi_row"])
        assert result is None

    def test_none_auto_confirms_all(self):
        """confirmed_deletions=None → ok (None)."""
        from app.engine.apply_engine import _validate_confirmed_deletions

        ir = self._make_ir_with_pending()
        result = _validate_confirmed_deletions(ir, None)
        assert result is None

    def test_empty_ir_no_pending(self):
        """Sin pending deletions → ok regardless."""
        from app.engine.apply_engine import _validate_confirmed_deletions

        ir = StructuralIR(
            contract_id="test", contract_version=1,
            capabilities=(), param_provenance={}, confidence=1.0,
        )
        result = _validate_confirmed_deletions(ir, [])
        assert result is None

    def test_double_gate_still_active(self):
        """_validate_delete_authority still runs as second gate."""
        from app.engine.apply_engine import _validate_delete_authority

        ir = self._make_ir_with_pending()
        plan = {"actions": [{"verb": "add", "target_capability": "presentation.kpi_row"}]}

        with pytest.raises(ValueError, match="DELETE.*no user-confirmed"):
            _validate_delete_authority(ir, plan)
