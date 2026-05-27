from __future__ import annotations

from typing import TYPE_CHECKING

from app.engine.errors import AmbiguousStructuralTargetError
from app.graphir.intent import is_capability_metadata
from app.graphir.structure.models import StructuralResolution
from app.graphir.structure.registry import StructuralRegistry

if TYPE_CHECKING:
    from app.engine.structural_completion import StructuralIR

GLOBAL_THRESHOLD: float = 0.5


def resolve(
    structural_ir: StructuralIR,
    registry: StructuralRegistry,
) -> StructuralResolution:
    """Resolver estructural mínimo + ambiguity gate.

    Para cada operación CREATE/MODIFY en StructuralIR:
      1. Clasifica: metadata (non_structural) → skip
      2. Busca candidatos en el registry (construido desde el contrato)
      3. Si hay匹配: score = 1.0, se resuelve el path
      4. Si no hay candidatos (structural_unknown) → skip, se registra en trace

    Siempre retorna un StructuralResolution. Si no se pudo resolver
    ninguna operación, reason lleva el motivo:
      - "no_structural_intents": todas las ops eran non_structural
      - "no_structural_targets": había estructurales pero ninguna en registry
      - "no_resolvable_operations": sin ops CREATE/MODIFY

    Raises:
        AmbiguousStructuralTargetError: si hay múltiples candidatos
            (>1) para una misma capability, o si score < GLOBAL_THRESHOLD.
    """
    contract_id = structural_ir.contract_id
    capability_to_path: dict[str, str] = {}
    trace: list[str] = []
    total_score = 0.0
    resolved_count = 0
    metadata_count = 0
    unknown_count = 0

    for op in structural_ir.operations:
        action: str = op["action"]
        target: str = op["target"]

        # Solo CREATE/MODIFY crean nodos
        if action not in ("CREATE", "MODIFY"):
            continue

        # Metadata capabilities no crean nodos GraphIR
        if is_capability_metadata(target):
            metadata_count += 1
            trace.append(f"op:{action}:{target} → non_structural (skipped)")
            continue

        candidates = registry.resolve_candidates(target)

        if not candidates:
            unknown_count += 1
            trace.append(
                f"op:{action}:{target} → no structural target in registry (skipped)"
            )
            continue

        if len(candidates) > 1:
            paths = [c.component_instance_path for c in candidates]
            raise AmbiguousStructuralTargetError(
                f"Multiple structural candidates for '{target}' "
                f"(action={action}). Found {len(candidates)} candidates: "
                f"{paths}. Cannot deterministically select."
            )

        # Scoring simple: el candidato del contrato tiene score = 1.0
        best = candidates[0]
        score = 1.0

        if score < GLOBAL_THRESHOLD:
            raise AmbiguousStructuralTargetError(
                f"Ambiguous target for '{target}': "
                f"best score {score:.2f} < threshold {GLOBAL_THRESHOLD}. "
                f"Candidate: {best.component_instance_path}"
            )

        capability_to_path[target] = best.component_instance_path
        total_score += score
        resolved_count += 1
        trace.append(
            f"op:{action}:{target} → {best.component_instance_path} "
            f"(score={score:.2f})"
        )

    if resolved_count == 0:
        if metadata_count > 0 and unknown_count == 0:
            reason = "no_structural_intents"
        elif unknown_count > 0:
            reason = "no_structural_targets"
        else:
            reason = "no_resolvable_operations"
        return StructuralResolution(
            capability_to_path={},
            confidence=1.0,
            trace=trace,
            reason=reason,
        )

    avg_confidence = total_score / resolved_count

    return StructuralResolution(
        capability_to_path=capability_to_path,
        confidence=avg_confidence,
        trace=trace,
        reason=None,
    )
