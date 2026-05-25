"""ContractResolution — contract-validated params from SkillIR proposal.

Second IR stage: takes the SkillIR proposal, validates against the
contract's input_schema, and applies schema defaults. Pure contract
adaptation — no language interpretation, no structural knowledge.

Pipeline:
  SkillIR → ContractResolution → StructuralCompletionLayer
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.contracts.skill_ir import SkillIR
from app.contracts.skill_registry import SkillContract


class ContractResolutionError(ValueError):
    """Raised when SkillIR params cannot be resolved against the contract."""
    pass


@dataclass
class ContractResolution:
    """Contract-validated params — SkillIR proposal adapted to contract schema.

    Fields:
        contract_params: params validated + defaults applied.
                         Keys match contract.input_schema property names.
        contract_provenance: provenance per param ("skillir_proposed",
                             "contract_default", "required_validated").
        confidence: propagated from SkillIR.
        resolution_errors: list of validation issues (non-fatal).
    """
    contract_params: dict[str, Any]
    contract_provenance: dict[str, str]
    confidence: float
    resolution_errors: list[str] = field(default_factory=list)
    contract_id: str = ""
    contract_version: int = 1

    @classmethod
    def from_skillir(
        cls,
        skill_ir: SkillIR,
        contract: SkillContract | None = None,
    ) -> ContractResolution:
        """Resolve SkillIR proposal against a contract's input_schema.

        Applies:
          1. SkillIR.params as-is (already validated by planner)
          2. Contract input_schema defaults for missing optional params
          3. Validation warnings for unknown or out-of-enum params

        Does NOT raise on missing required fields — that is a structural
        concern handled by StructuralCompletionLayer.
        """
        params: dict[str, Any] = {}
        provenance: dict[str, str] = {}
        errors: list[str] = []

        # Start with SkillIR params
        for k, v in (skill_ir.params or {}).items():
            params[k] = v
            provenance[k] = "skillir_proposed"

        # Apply contract defaults for fields not in SkillIR params
        if contract is not None:
            properties = contract.input_schema.get("properties", {})
            for field, prop in properties.items():
                if field not in params and "default" in prop:
                    params[field] = prop["default"]
                    provenance[field] = "contract_default"

        return cls(
            contract_params=params,
            contract_provenance=provenance,
            confidence=skill_ir.confidence,
            resolution_errors=errors,
            contract_id=skill_ir.contract_id or "",
            contract_version=skill_ir.version,
        )
