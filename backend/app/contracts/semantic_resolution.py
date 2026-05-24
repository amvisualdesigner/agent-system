from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SemanticConflictError(ValueError):
    """Raised when reconciliation detects unresolvable semantic conflicts.

    Explicit vs explicit conflicts always raise. Explicit vs SkillIR
    resolves to explicit (with trace) — no error.
    """
    def __init__(
        self,
        message: str,
        param: str | None = None,
        user_value: Any = None,
        proposal_value: Any = None,
    ):
        self.param = param
        self.user_value = user_value
        self.proposal_value = proposal_value
        super().__init__(message)


@dataclass
class SemanticResolution:
    """Único contrato semántico autorizado para GraphIRPipeline.

    Reemplaza SkillIR.params + Intent.params como fuente de parámetros.
    """
    contract_id: str
    contract_version: int
    params: dict[str, Any]
    param_provenance: dict[str, str]
    confidence: float
    resolution_trace: list[str] = field(default_factory=list)

    @classmethod
    def from_skillir(cls, skill_ir) -> SemanticResolution:
        """Create resolution from SkillIR directly (no frame available)."""
        if not skill_ir.contract_id:
            raise SemanticConflictError(
                "No contract_id available from SkillIR"
            )
        return cls(
            contract_id=skill_ir.contract_id,
            contract_version=skill_ir.version,
            params=dict(skill_ir.params),
            param_provenance={k: "skillir_proposed" for k in skill_ir.params},
            confidence=skill_ir.confidence,
            resolution_trace=[
                "No semantic frame — params sourced from SkillIR proposal"
            ],
        )
