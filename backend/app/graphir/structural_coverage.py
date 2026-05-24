"""StructuralCoverageValidator — StructuralIR integrity validation.

NO intents. NO keywords. NO decomposition confidence.
Only structural coherence and contract compliance.

Reemplaza a IntentCoverageValidator en el NEW PIPELINE path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.engine.structural_completion import (
    CompletionMode,
    StructuralIR,
    STRUCTURAL_SCHEMA,
)
from app.contracts.skill_registry import SkillContract


class StructuralIntegrityError(Exception):
    """Raised for structural violations that cannot be recovered.

    Ghost capability (not in contract, not augmentable)
    Missing required field without fallback (invalid IR)
    """


@dataclass
class StructuralCoverageReport:
    """Validation result for StructuralIR integrity.

    Attributes:
        is_valid: True if no StructuralIntegrityError was raised.
            Warnings do NOT invalidate — partial structure is valid.
        completeness: ratio of SAFE_COMPLETE / total capabilities.
        safe_skip_count: number of capabilities skipped due to missing resolution.
        ghost_capabilities: capabilities not found in contract or schemas.
        warnings: list of warning messages for SAFE_SKIP and fallback usage.
    """
    is_valid: bool = True
    completeness: float = 1.0
    safe_skip_count: int = 0
    ghost_capabilities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class StructuralCoverageValidator:
    """PURE function: StructuralIR → StructuralCoverageReport.

    Validates:
      1. Contract completeness — SAFE_COMPLETE capabilities have required fields
      2. SAFE_SKIP correctness — only when required missing + fallback defined
      3. No ghost capabilities — all capabilities are known in STRUCTURAL_SCHEMA

    No side effects. No state. Deterministic.
    """

    @staticmethod
    def validate(
        ir: StructuralIR,
        contract: SkillContract | None = None,
        known_capabilities: set[str] | None = None,
    ) -> StructuralCoverageReport:
        """Validate StructuralIR integrity.

        Args:
            ir: StructuralIR to validate.
            contract: Optional SkillContract for contract capability validation.
            known_capabilities: Optional override for allowed capabilities.
                If not provided, derived from STRUCTURAL_SCHEMA + contract.

        Returns:
            StructuralCoverageReport with validation results.

        Raises:
            StructuralIntegrityError: on ghost capabilities or invalid IR.
        """
        if known_capabilities is None:
            known_capabilities = set(STRUCTURAL_SCHEMA.keys())
            if contract is not None:
                contract_caps = set(
                    contract.ast_template.get("capabilities", {}).values()
                )
                known_capabilities |= contract_caps

        warnings: list[str] = []
        ghost_caps: list[str] = []
        safe_skip_count = 0
        safe_complete_count = 0

        for rc in ir.capabilities:
            # ── Ghost capability check ──
            if rc.name not in known_capabilities:
                ghost_caps.append(rc.name)
                continue

            schema = STRUCTURAL_SCHEMA.get(rc.name)

            if rc.mode == CompletionMode.SAFE_SKIP:
                safe_skip_count += 1
                if schema is not None:
                    required = schema.get("required", [])
                    missing = [r for r in required if r not in rc.params]
                    if not missing:
                        warnings.append(
                            f"{rc.name}: SAFE_SKIP but no required fields missing — "
                            f"possibly unnecessary skip"
                        )
                else:
                    warnings.append(f"{rc.name}: SAFE_SKIP, no schema found")
                continue

            if rc.mode == CompletionMode.SAFE_COMPLETE:
                safe_complete_count += 1
                if schema is None:
                    warnings.append(
                        f"{rc.name}: SAFE_COMPLETE but no schema — "
                        f"cannot validate required fields"
                    )
                    continue

                required = schema.get("required", [])
                missing = [r for r in required if r not in rc.params]
                if missing:
                    fallback = schema.get("safe_fallback", {})
                    missing_no_fallback = [r for r in missing if r not in fallback]
                    if missing_no_fallback:
                        raise StructuralIntegrityError(
                            f"{rc.name}: required fields {missing_no_fallback} "
                            f"missing with no fallback available"
                        )
                    warnings.append(
                        f"{rc.name}: required fields {missing} missing — "
                        f"using safe fallback"
                    )

        if ghost_caps:
            raise StructuralIntegrityError(
                f"Ghost capabilities not in contract or schemas: {ghost_caps}"
            )

        total = len(ir.capabilities)
        completeness = safe_complete_count / total if total > 0 else 1.0

        return StructuralCoverageReport(
            is_valid=True,
            completeness=completeness,
            safe_skip_count=safe_skip_count,
            ghost_capabilities=ghost_caps,
            warnings=warnings,
        )
